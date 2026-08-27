"""Protect the static dashboard export and dependency-free browser contract."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re

import pytest

from src import export_temperature_dashboard as dashboard


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_TIME = datetime(2026, 8, 24, 18)
PLRS = (
    ("01100101", "Stülerstraße", 700, 1_000),
    ("01100102", "Großer Tiergarten", 291, 520),
)


class Result:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, *, with_history=True, incomplete_history=False):
        self.with_history = with_history
        self.incomplete_history = incomplete_history
        self.queries = []

    def execute(self, query, parameters=None):
        self.queries.append((query, parameters))
        if query == dashboard.SCHEMA_QUERY:
            return Result(row=(True, True, True, self.with_history, len(PLRS)))
        if query == dashboard.SUMMARY_QUERY:
            rows = []
            for index, (plr_id, name, older, total) in enumerate(PLRS):
                x = 390_000 + index * 1_100
                geometry = {
                    "type": "MultiPolygon",
                    "coordinates": [[[[x, 5_810_000], [x + 1_000, 5_810_000], [x + 1_000, 5_811_000], [x, 5_811_000], [x, 5_810_000]]]],
                }
                rows.append(
                    (
                        plr_id,
                        name,
                        RUN_TIME,
                        29.25 + index,
                        datetime(2026, 8, 25, 15),
                        4.25 + index,
                        datetime(2026, 8, 25, 15),
                        2.75 + index,
                        datetime(2026, 8, 25, 12),
                        31.5 + index,
                        total,
                        older,
                        "available",
                        geometry,
                    )
                )
            assert parameters == (20.0,)
            return Result(rows=rows)
        if query == dashboard.FORECAST_QUERY:
            times = [f"2026-08-{24 + (hour + 18) // 24:02d}T{(hour + 18) % 24:02d}:00" for hour in range(25)]
            return Result(
                rows=[
                    (
                        plr_id,
                        times,
                        [20 + hour / 4 for hour in range(25)],
                        [21 + hour / 4 for hour in range(25)],
                        [19.0] * 25,
                        [20.0] * 25,
                        25,
                    )
                    for plr_id, *_ in PLRS
                ]
            )
        if query == dashboard.HISTORY_QUERY:
            years = [
                {
                    "year": year,
                    "temperatures_c": [year / 100 + hour / 10 for hour in range(25)],
                    "apparent_temperatures_c": [
                        year / 100 + hour / 10 + 1 for hour in range(25)
                    ],
                }
                for year in range(1995, 2026)
            ]
            if self.incomplete_history:
                years = years[:-1]
            return Result(rows=[(plr_id, years, True) for plr_id, *_ in PLRS])
        raise AssertionError(f"Unexpected dashboard query: {query}")


def install_connection(monkeypatch, *, with_history=True, incomplete_history=False):
    connection = Connection(
        with_history=with_history,
        incomplete_history=incomplete_history,
    )

    @contextmanager
    def fake_database_connection(**kwargs):
        yield connection

    monkeypatch.setattr(dashboard, "database_connection", fake_database_connection)
    return connection


def test_static_export_writes_one_map_and_lazy_detail_per_plr(tmp_path, monkeypatch):
    install_connection(monkeypatch)
    destination = tmp_path / "berlin-temperature"

    result = dashboard.export_temperature_dashboard(output_dir=destination)

    assert result == {
        "status": "ready",
        "output_directory": str(destination.resolve()),
        "run_time_berlin": "2026-08-24T18:00",
        "plr_count": 2,
        "lead_hour_count": 25,
        "historical_year_count": 31,
        "detail_file_count": 2,
        "simplification_meters": 20.0,
    }
    assert all((destination / name).is_file() for name in dashboard.PUBLIC_ASSETS)

    map_payload = json.loads((destination / "data" / "map.json").read_text())
    detail = json.loads(
        (destination / "data" / "areas" / "01100101.json").read_text()
    )
    assert map_payload["coordinate_system"] == "EPSG:25833"
    assert map_payload["format_version"] == 2
    assert map_payload["historical_year_count"] == 31
    assert len(map_payload["areas"]) == 2
    assert map_payload["areas"][0]["geometry"]["type"] == "MultiPolygon"
    assert len(detail["forecast_temperatures_c"]) == 25
    assert len(detail["forecast_apparent_temperatures_c"]) == 25
    assert len(detail["historical_median_temperatures_c"]) == 25
    assert len(detail["historical_median_apparent_temperatures_c"]) == 25
    assert len(detail["historical_years"]) == 31
    assert len(detail["historical_years"][0]["temperatures_c"]) == 25
    assert len(detail["historical_years"][0]["apparent_temperatures_c"]) == 25


def test_snapshot_only_export_remains_useful_without_individual_years(tmp_path, monkeypatch):
    connection = install_connection(monkeypatch, with_history=False)

    result = dashboard.export_temperature_dashboard(output_dir=tmp_path)
    detail = json.loads((tmp_path / "data" / "areas" / "01100101.json").read_text())

    assert result["historical_year_count"] == 0
    assert detail["historical_years"] == []
    assert dashboard.HISTORY_QUERY not in [query for query, _ in connection.queries]


def test_export_rejects_partial_historical_year_coverage(tmp_path, monkeypatch):
    install_connection(monkeypatch, incomplete_history=True)

    with pytest.raises(RuntimeError, match="all historical years 1995-2025"):
        dashboard.export_temperature_dashboard(output_dir=tmp_path)


@pytest.mark.parametrize("simplify", [0, -1, float("inf"), float("nan")])
def test_export_rejects_unsafe_geometry_simplification(simplify):
    with pytest.raises(ValueError, match="positive meter value"):
        dashboard.export_temperature_dashboard(simplify_meters=simplify)


def test_browser_assets_are_dependency_free_and_load_details_lazily():
    root = PROJECT_ROOT / "web" / "temperature-dashboard"
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "app.js").read_text(encoding="utf-8")
    stylesheet = (root / "styles.css").read_text(encoding="utf-8")
    combined = html + javascript + stylesheet

    assert 'src="app.js?v=6"' in html
    assert 'href="styles.css?v=6"' in html
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert "fetch(\"http" not in javascript
    assert "@import" not in stylesheet
    assert "leaflet" not in combined.lower()
    assert "mapbox" not in combined.lower()
    assert "d3." not in combined.lower()
    assert 'fetch("data/map.json"' in javascript
    assert "data/areas/${plrId}.json" in javascript
    assert 'addEventListener("pointerenter"' in javascript
    assert "historical_years" in javascript
    assert "forecast_apparent_temperatures_c" in javascript
    assert "apparent_temperatures_c" in javascript
    assert "forecast-line" in stylesheet
    assert "comparison-line" in stylesheet
    assert "history-line" in stylesheet
    assert "Highest forecast temperature" in html
    assert "Highest difference between apparent and forecast temperature" in html
    assert "Residents aged 65+" in html
    assert "Forecast apparent temperature" in html
    assert "Forecast temperature" in html
    assert 'comparisonKey: "forecast_apparent_temperatures_c"' in javascript
    assert 'comparisonKey: "forecast_temperatures_c"' in javascript
    assert 'class: "comparison-line"' in javascript
    assert "if (showComparison) tooltip.append(comparison)" in javascript


def test_every_browser_element_reference_is_registered_before_use():
    javascript = (
        PROJECT_ROOT / "web" / "temperature-dashboard" / "app.js"
    ).read_text(encoding="utf-8")

    registry = re.search(r"for \(const id of \[(.*?)\]\)", javascript, re.S)
    assert registry is not None

    registered = set(re.findall(r'"([a-z][a-z-]+)"', registry.group(1)))
    referenced = set(re.findall(r'elements\["([a-z][a-z-]+)"\]', javascript))

    assert referenced <= registered, (
        "Dashboard elements are referenced before registration: "
        f"{', '.join(sorted(referenced - registered))}"
    )
    assert "chart-wrap" in registered


def test_map_click_pins_selection_until_escape_restores_hover():
    javascript = (
        PROJECT_ROOT / "web" / "temperature-dashboard" / "app.js"
    ).read_text(encoding="utf-8")
    html = (
        PROJECT_ROOT / "web" / "temperature-dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    assert 'path.addEventListener("pointerenter", () => scheduleArea(area.id))' in javascript
    assert (
        'path.addEventListener("click", () => selectArea(area.id, { pin: true }))'
        in javascript
    )
    assert 'if (options.pin)' in javascript
    assert 'window.clearTimeout(state.hoverTimer)' in javascript
    assert 'if (!state.pinned) selectArea(plrId)' in javascript
    assert 'if (event.key === "Escape") releasePin()' in javascript
    assert 'Click to pin · Press Esc to unpin' in html
    assert "The next 24h hours, compared to the past three decades." in html
    assert "Largest difference vs. median" in html


def test_hidden_loading_layers_cannot_intercept_map_or_chart_pointer_events():
    root = PROJECT_ROOT / "web" / "temperature-dashboard"
    javascript = (root / "app.js").read_text(encoding="utf-8")
    stylesheet = (root / "styles.css").read_text(encoding="utf-8")

    assert '[hidden] { display: none !important; }' in stylesheet
    assert 'elements["map-loading"].hidden = true' in javascript
    assert 'elements["chart-loading"].hidden = true' in javascript
    assert 'elements[specification.legend].hidden = historical.length === 0' in javascript
    assert 'elements[specification.note].hidden = historical.length !== 0' in javascript
    assert 'elements["apparent-chart-section"].hidden = !compareApparent' in javascript
    assert 'elements[specification.comparisonLegend].hidden = !compareApparent' in javascript


def test_generated_dashboard_data_remains_out_of_git():
    ignore = (
        PROJECT_ROOT / "web" / "temperature-dashboard" / "data" / ".gitignore"
    ).read_text(encoding="utf-8")

    assert ignore.splitlines() == ["*", "!.gitignore"]
