from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

from src.forecast_key import DWD_RUN_FORMAT, ForecastKey
from src.icon_d2_ruc_indicators import get_indicator


BASE_URL = (
    "https://opendata.dwd.de/weather/nwp/v1/m/"
    "icon-d2-ruc/p"
)
DEFAULT_TIMEOUT_SECONDS = 120
RUN_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "berlin_capstone_icon_d2_ruc/2.0"
            )
        }
    )
    return session


def run_index_url(indicator: str) -> str:
    get_indicator(indicator)
    return f"{BASE_URL}/{indicator}/r/"


def field_url(
    indicator: str,
    forecast: ForecastKey,
) -> str:
    get_indicator(indicator)

    dwd_run_label = forecast.run_time.strftime(
        DWD_RUN_FORMAT
    )

    return (
        f"{BASE_URL}/{indicator}/r/"
        f"{dwd_run_label}/s/"
        f"{forecast.lead_time_label}.grib2"
    )


def advertised_run_times(
    session: requests.Session,
    indicator: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[datetime]:
    response = session.get(
        run_index_url(indicator),
        timeout=timeout_seconds,
    )
    response.raise_for_status()

    decoded_html = unquote(response.text)
    labels = set(RUN_PATTERN.findall(decoded_html))

    return sorted(
        (
            datetime.strptime(
                label,
                DWD_RUN_FORMAT,
            ).replace(tzinfo=timezone.utc)
            for label in labels
        ),
        reverse=True,
    )


def field_available(
    session: requests.Session,
    *,
    indicator: str,
    forecast: ForecastKey,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    url = field_url(indicator, forecast)

    try:
        response = session.head(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
        )

        if response.status_code == 405:
            response = session.get(
                url,
                stream=True,
                timeout=timeout_seconds,
                allow_redirects=True,
            )

        return response.ok

    except requests.RequestException:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()


def download_field(
    session: requests.Session,
    *,
    indicator: str,
    forecast: ForecastKey,
    target: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    overwrite: bool = False,
) -> dict:
    """
    Download one ICON D2 RUC field atomically.

    The function intentionally knows nothing about whether other
    indicators for the same forecast are available. That orchestration
    decision belongs to the future Dagster sensor.
    """
    contract = get_indicator(indicator)
    url = field_url(indicator, forecast)

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists"
        )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    part_path = target.with_suffix(
        target.suffix + ".part"
    )

    if part_path.exists():
        part_path.unlink()

    started_at = utc_now()

    try:
        with session.get(
            url,
            stream=True,
            timeout=timeout_seconds,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()

            with part_path.open("wb") as handle:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    if chunk:
                        handle.write(chunk)

            if part_path.stat().st_size == 0:
                raise RuntimeError(
                    f"DWD returned an empty file for {url}"
                )

            part_path.replace(target)

            return {
                "indicator": contract.name,
                "dwd_parameter_id": (
                    contract.dwd_parameter_id
                ),
                "run_time_utc": (
                    forecast.run_time.isoformat()
                ),
                "lead_time": (
                    forecast.lead_time_label
                ),
                "valid_time_utc": (
                    forecast.valid_time.isoformat()
                ),
                "requested_url": url,
                "resolved_url": response.url,
                "http_status": response.status_code,
                "content_type": response.headers.get(
                    "Content-Type"
                ),
                "content_length_header": (
                    response.headers.get(
                        "Content-Length"
                    )
                ),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "started_at_utc": started_at,
                "completed_at_utc": utc_now(),
            }

    except Exception:
        if part_path.exists():
            part_path.unlink()
        raise
