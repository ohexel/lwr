from collections import Counter
from dataclasses import replace

import pytest
import requests

from src.hostrada_snapshot import SnapshotManifest, sha256_file
from src import plr_display_names


def test_official_workbook_matches_all_plrs_and_preserves_real_display_names():
    manifest = SnapshotManifest.load()

    names = plr_display_names.read_plr_display_names(
        plr_display_names.BUNDLED_WORKBOOK,
        manifest,
    )

    assert len(names) == 542
    assert names[:4] == [
        ("01100101", "Stülerstraße"),
        ("01100102", "Großer Tiergarten"),
        ("01100103", "Lützowstraße"),
        ("01100104", "Körnerstraße"),
    ]
    assert Counter(name for _, name in names)["Schloßstraße"] == 2
    assert (
        sha256_file(plr_display_names.BUNDLED_WORKBOOK)
        == plr_display_names.WORKBOOK_SHA256
    )


def test_workbook_rejects_html_and_mismatched_reference_geography(tmp_path):
    html_path = tmp_path / "not-a-workbook.xlsx"
    html_path.write_text("<html>Source unavailable</html>", encoding="utf-8")
    manifest = SnapshotManifest.load()

    with pytest.raises(ValueError, match="not a valid Excel workbook"):
        plr_display_names.read_plr_display_names(html_path, manifest)

    wrong_geography = replace(manifest, sorted_plr_ids_sha256="0" * 64)
    with pytest.raises(ValueError, match="do not match"):
        plr_display_names.read_plr_display_names(
            plr_display_names.BUNDLED_WORKBOOK,
            wrong_geography,
        )


def test_unavailable_official_workbook_uses_verified_bundled_fallback(
    tmp_path,
    monkeypatch,
):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("official source unavailable")

    monkeypatch.setattr(plr_display_names.requests, "get", unavailable)

    workbook_path, acquisition_mode = plr_display_names.acquire_plr_name_workbook(
        SnapshotManifest.load(),
        project_root=tmp_path,
    )

    assert acquisition_mode == "bundled_fallback"
    assert sha256_file(workbook_path) == plr_display_names.WORKBOOK_SHA256


def test_offline_workbook_acquisition_never_contacts_the_official_source(
    tmp_path,
    monkeypatch,
):
    def must_not_download(*args, **kwargs):
        raise AssertionError("offline mode contacted the official source")

    monkeypatch.setattr(plr_display_names.requests, "get", must_not_download)

    _, acquisition_mode = plr_display_names.acquire_plr_name_workbook(
        SnapshotManifest.load(),
        project_root=tmp_path,
        offline=True,
    )

    assert acquisition_mode == "bundled_fallback"
