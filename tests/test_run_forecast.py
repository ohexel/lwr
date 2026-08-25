"""Protect the public manual-forecast interface and source availability gate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src import run_forecast as forecast_runner
from src.dwd_weather_availability import REQUIRED_WEATHER_INDICATORS
from src.forecast_key import ForecastKey, ProjectPaths
from src.run_forecast import (
    ForecastUnavailableError,
    ensure_forecast_sources_available,
    main,
    run_forecast,
)


@dataclass
class FakeResponse:
    ok: bool

    @property
    def status_code(self) -> int:
        return 200 if self.ok else 404


class FakeSession:
    def __init__(self, unavailable: set[str] | None = None) -> None:
        self.unavailable = unavailable or set()
        self.requested_urls: list[str] = []

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def head(self, url: str, **kwargs: object) -> FakeResponse:
        del kwargs
        self.requested_urls.append(url)
        indicator = url.split("/p/", 1)[1].split("/r/", 1)[0]
        return FakeResponse(ok=indicator not in self.unavailable)


def forecast_fixture() -> ForecastKey:
    return ForecastKey.from_dwd_labels(
        run_time="2026-08-24T16:00",
        lead_time="PT000H00M",
    )


def test_unavailable_forecast_has_actionable_utc_source_error(
    tmp_path: Path,
) -> None:
    session = FakeSession(unavailable={"T_2M", "U_10M"})

    with pytest.raises(ForecastUnavailableError) as observed:
        ensure_forecast_sources_available(
            forecast_fixture(),
            project_root=tmp_path,
            session_factory=lambda: session,
        )

    message = str(observed.value)
    assert "2026-08-24 16:00 UTC" in message
    assert "Recent runs may not yet be published" in message
    assert "older runs may no longer be retained" in message
    assert "Unavailable fields: T_2M, U_10M" in message
    assert "opendata.dwd.de" in message


def test_retained_forecast_remains_reprocessable_without_dwd(
    tmp_path: Path,
) -> None:
    forecast = forecast_fixture()
    paths = ProjectPaths(project_root=tmp_path)

    for indicator in REQUIRED_WEATHER_INDICATORS:
        retained_path = paths.raw_icon_field(
            indicator=indicator,
            forecast=forecast,
        )
        retained_path.parent.mkdir(parents=True)
        retained_path.write_bytes(b"retained fixture")

    def forbidden_network_session() -> FakeSession:
        raise AssertionError("Retained raw partitions must not require DWD")

    ensure_forecast_sources_available(
        forecast,
        project_root=tmp_path,
        session_factory=forbidden_network_session,
    )


def test_only_missing_retained_fields_require_upstream_availability(
    tmp_path: Path,
) -> None:
    forecast = forecast_fixture()
    paths = ProjectPaths(project_root=tmp_path)
    retained_path = paths.raw_icon_field(indicator="T_2M", forecast=forecast)
    retained_path.parent.mkdir(parents=True)
    retained_path.write_bytes(b"retained fixture")
    session = FakeSession()

    ensure_forecast_sources_available(
        forecast,
        project_root=tmp_path,
        session_factory=lambda: session,
    )

    assert len(session.requested_urls) == len(REQUIRED_WEATHER_INDICATORS) - 1
    assert all("/T_2M/" not in url for url in session.requested_urls)


def test_invalid_run_label_fails_before_contacting_dwd() -> None:
    with pytest.raises(ValueError, match="YYYYMMDDTHHMM in UTC"):
        run_forecast("2026-08-24 16:00", "PT000H00M")


def test_expired_run_fails_before_contacting_dwd() -> None:
    with pytest.raises(ForecastUnavailableError, match="outside the configured 24-hour"):
        run_forecast("19950101T0000", "PT000H00M")


def test_future_run_explains_utc_without_contacting_dwd() -> None:
    with pytest.raises(ForecastUnavailableError, match="UTC, not Berlin-local"):
        run_forecast("29990101T0000", "PT000H00M")


def test_manual_forecast_error_is_concise_and_traceback_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable_run(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ForecastUnavailableError("Recent DWD run has not been published.")

    monkeypatch.setattr(forecast_runner, "run_forecast", unavailable_run)

    with pytest.raises(SystemExit) as observed:
        main(["--run-time", "20260824T1600"])

    assert observed.value.code == 1
    error = capsys.readouterr().err
    assert "error: Recent DWD run has not been published." in error
    assert "Traceback" not in error


def test_manual_forecast_help_explains_utc_and_publication_window(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as observed:
        main(["--help"])

    assert observed.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "UTC, not Berlin-local time" in output
    assert "Recent runs may not yet be published" in output
    assert "older runs may no longer be available" in output
