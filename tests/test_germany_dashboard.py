"""Germany map (M5): exporter helpers, local Range server, page contracts."""

from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
import threading
import urllib.error
import urllib.request

import pytest

from src.export_germany_dashboard import (
    DASHBOARD_SOURCE,
    EXPECTED_LEAD_COUNT,
    PUBLIC_ASSETS,
    rollup_records,
)
from src.germany.preview_server import RangeRequestHandler, parse_range


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Range parsing and the local preview server
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=900-", 1000, (900, 999)),
        ("bytes=990-2000", 1000, (990, 999)),
        ("bytes=-10", 1000, (990, 999)),
        ("bytes=-5000", 1000, (0, 999)),
        ("bytes=1000-", 1000, None),
        ("bytes=5-2", 1000, None),
        ("bytes=-0", 1000, None),
        ("bytes=-", 1000, None),
        ("bytes=0-1,5-6", 1000, None),  # multiple ranges are not served
        ("items=0-1", 1000, None),
        ("bytes=0-0", 0, None),
    ],
)
def test_parse_range(header: str, size: int, expected) -> None:
    assert parse_range(header, size) == expected


def test_preview_server_answers_range_requests(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 40
    (tmp_path / "tiles.pmtiles").write_bytes(payload)
    class QuietHandler(RangeRequestHandler):
        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/tiles.pmtiles"
    try:
        request = urllib.request.Request(base, headers={"Range": "bytes=100-163"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == f"bytes 100-163/{len(payload)}"
            assert response.read() == payload[100:164]

        request = urllib.request.Request(base, headers={"Range": "bytes=-16"})
        with urllib.request.urlopen(request) as response:
            assert response.read() == payload[-16:]

        with urllib.request.urlopen(base) as response:  # no Range: whole file
            assert response.status == 200
            assert response.read() == payload

        request = urllib.request.Request(base, headers={"Range": f"bytes={len(payload)}-"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 416
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Roll-up maxima
# ---------------------------------------------------------------------------
def _rows(key, apparent, population_weighted=True):
    return [
        (key, "Name", "99", hour, apparent[hour] + 1, apparent[hour] + 1 if population_weighted else None,
         apparent[hour], apparent[hour] if population_weighted else None, 1000, 3)
        for hour in range(EXPECTED_LEAD_COUNT)
    ]


def test_rollup_records_take_first_maximum_and_its_hour() -> None:
    apparent = [0.0] * EXPECTED_LEAD_COUNT
    apparent[5] = 7.123
    apparent[9] = 7.123  # tie: the earlier hour wins, as in the SQL summary
    record = rollup_records(_rows("99001", apparent))["99001"]
    assert record["a_area"] == 7.12
    assert record["a_area_h"] == 5
    assert record["t_area"] == 8.12
    assert record["l"] == "99"


def test_rollup_records_without_population_weights_are_none_not_zero() -> None:
    record = rollup_records(_rows("99001", [1.0] * EXPECTED_LEAD_COUNT, False))["99001"]
    assert record["a_pop"] is None
    assert record["a_pop_h"] is None
    assert record["a_area"] == 1.0


def test_rollup_records_reject_incomplete_runs() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        rollup_records(_rows("99001", [1.0] * EXPECTED_LEAD_COUNT)[:-1])


# ---------------------------------------------------------------------------
# Page contracts (D6, handover section 8)
# ---------------------------------------------------------------------------
def _page() -> str:
    return "\n".join(
        (DASHBOARD_SOURCE / name).read_text(encoding="utf-8") for name in PUBLIC_ASSETS
    )


@pytest.mark.contract
def test_libraries_are_pinned_with_subresource_integrity() -> None:
    html = (DASHBOARD_SOURCE / "index.html").read_text(encoding="utf-8")
    external = re.findall(r'(?:src|href)="(https://cdn[^"]+)"', html)
    assert external, "MapLibre and PMTiles are loaded from a CDN"
    for url in external:
        assert re.search(r"@\d+\.\d+\.\d+/", url), url
    tags = re.findall(r"<(?:script|link)[^>]+https://cdn[^>]+>", html, flags=re.S)
    for tag in tags:
        assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, tag


@pytest.mark.contract
def test_no_third_party_basemap_or_api_key() -> None:
    page = _page().lower()
    for word in ("api_key", "apikey", "access_token", "mapbox://", "maptiler",
                 "tiles.openstreetmap", "{z}/{x}/{y}", "glyphs", "sprite"):
        assert word not in page, word


@pytest.mark.contract
def test_required_attribution_is_shown() -> None:
    html = (DASHBOARD_SOURCE / "index.html").read_text(encoding="utf-8")
    assert "© GeoBasis-DE / BKG 2022 (Daten verändert)" in html
    assert "https://www.bkg.bund.de" in html
    assert "Zensus 2022, © Statistische Ämter des Bundes und der Länder" in html
    assert "Deutscher Wetterdienst" in html
    assert html.count("dl-de/by-2-0") >= 2


@pytest.mark.contract
def test_level_switch_is_keyboard_operable_and_values_are_not_classified() -> None:
    html = (DASHBOARD_SOURCE / "index.html").read_text(encoding="utf-8")
    for level in ("lan", "krs", "gem"):
        assert f'type="radio" name="level" value="{level}"' in html
    assert 'id="region-search"' in html
    script = (DASHBOARD_SOURCE / "app.js").read_text(encoding="utf-8")
    # Continuous colour scale (ColorBrewer YlOrRd), no warning classes.
    assert '"interpolate", ["linear"], ["feature-state", "v"]' in script
    for word in ("warning", "danger", "risk", "alert"):
        assert word not in script.lower()


@pytest.mark.contract
def test_generated_data_is_not_tracked_and_berlin_dashboard_is_separate() -> None:
    ignore = (DASHBOARD_SOURCE / "data" / ".gitignore").read_text(encoding="utf-8")
    assert ignore.splitlines() == ["*", "!.gitignore"]
    assert DASHBOARD_SOURCE != PROJECT_ROOT / "web" / "temperature-dashboard"
    assert "temperature-dashboard" not in _page()
