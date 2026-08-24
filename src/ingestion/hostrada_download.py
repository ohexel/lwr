"""Bounded, restart-safe downloads of one complete HOSTRADA source month."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import perf_counter
from typing import Callable

import requests

from src.hostrada_contract import HOSTRADA_FIELD_CONTRACTS, HostradaMonthKey
from src.hostrada_paths import HostradaPaths, hostrada_source_url


LOGGER = logging.getLogger(__name__)
DEFAULT_DOWNLOAD_ATTEMPTS = 3
DEFAULT_CONNECT_TIMEOUT_SECONDS = 15
DEFAULT_READ_TIMEOUT_SECONDS = 120
DEFAULT_RETRY_DELAY_SECONDS = 5.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HostradaSourceDownload:
    variable_name: str
    source_path: Path
    source_url: str
    source_size_bytes: int
    downloaded: bool
    attempts: int


@dataclass(frozen=True)
class HostradaMonthDownload:
    source_month: str
    sources: tuple[HostradaSourceDownload, ...]
    duration_seconds: float

    @property
    def downloaded_file_count(self) -> int:
        return sum(source.downloaded for source in self.sources)

    @property
    def reused_file_count(self) -> int:
        return len(self.sources) - self.downloaded_file_count

    @property
    def source_size_bytes(self) -> int:
        return sum(source.source_size_bytes for source in self.sources)


def _raise_if_stopped(stop_event: Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("HOSTRADA source download was cancelled")


def _retryable_failure(error: Exception) -> bool:
    if isinstance(error, requests.HTTPError):
        response = error.response
        return response is not None and (
            response.status_code in RETRYABLE_HTTP_STATUS
        )
    return isinstance(error, requests.RequestException)


def _retry_delay_seconds(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    retry_after = None if response is None else response.headers.get("Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return min(float(retry_after), 300.0)
    return min(DEFAULT_RETRY_DELAY_SECONDS * 2 ** (attempt - 1), 60.0)


def _download_source(
    session: requests.Session,
    *,
    month: HostradaMonthKey,
    variable_name: str,
    paths: HostradaPaths,
    max_attempts: int,
    stop_event: Event | None,
    check_free_space: Callable[[], None] | None,
) -> HostradaSourceDownload:
    target = paths.source_file(month, variable_name)
    url = hostrada_source_url(month, variable_name)

    if target.exists():
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(f"Existing HOSTRADA source is invalid: {target}")
        LOGGER.info(
            "hostrada_source_reused month=%s variable=%s bytes=%s path=%s",
            month.partition_key,
            variable_name,
            target.stat().st_size,
            target,
        )
        return HostradaSourceDownload(
            variable_name=variable_name,
            source_path=target,
            source_url=url,
            source_size_bytes=target.stat().st_size,
            downloaded=False,
            attempts=0,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")

    for attempt in range(1, max_attempts + 1):
        _raise_if_stopped(stop_event)
        if check_free_space is not None:
            check_free_space()
        partial.unlink(missing_ok=True)

        try:
            with session.get(
                url,
                stream=True,
                allow_redirects=True,
                timeout=(
                    DEFAULT_CONNECT_TIMEOUT_SECONDS,
                    DEFAULT_READ_TIMEOUT_SECONDS,
                ),
            ) as response:
                response.raise_for_status()
                observed_bytes = 0

                with partial.open("wb") as output:
                    for block in response.iter_content(
                        chunk_size=DOWNLOAD_CHUNK_BYTES
                    ):
                        _raise_if_stopped(stop_event)
                        if block:
                            output.write(block)
                            observed_bytes += len(block)

                if observed_bytes == 0:
                    raise RuntimeError(f"HOSTRADA source is empty: {url}")

                content_length = response.headers.get("Content-Length")
                if content_length is not None and content_length.isdigit():
                    expected_bytes = int(content_length)
                    if observed_bytes != expected_bytes:
                        raise requests.RequestException(
                            "HOSTRADA download size does not match "
                            f"Content-Length: {observed_bytes} != {expected_bytes}"
                        )

                _raise_if_stopped(stop_event)
                partial.replace(target)

            LOGGER.info(
                "hostrada_source_downloaded month=%s variable=%s "
                "bytes=%s attempts=%s path=%s",
                month.partition_key,
                variable_name,
                observed_bytes,
                attempt,
                target,
            )
            return HostradaSourceDownload(
                variable_name=variable_name,
                source_path=target,
                source_url=url,
                source_size_bytes=observed_bytes,
                downloaded=True,
                attempts=attempt,
            )

        except Exception as error:
            partial.unlink(missing_ok=True)
            if attempt == max_attempts or not _retryable_failure(error):
                raise

            delay = _retry_delay_seconds(error, attempt)
            LOGGER.warning(
                "hostrada_source_retry month=%s variable=%s attempt=%s "
                "max_attempts=%s delay_seconds=%.1f error=%s",
                month.partition_key,
                variable_name,
                attempt,
                max_attempts,
                delay,
                error,
            )
            if stop_event is None:
                Event().wait(delay)
            elif stop_event.wait(delay):
                _raise_if_stopped(stop_event)

    raise AssertionError("HOSTRADA source download exhausted unexpectedly")


def download_hostrada_month(
    month: HostradaMonthKey,
    paths: HostradaPaths | None = None,
    *,
    max_attempts: int = DEFAULT_DOWNLOAD_ATTEMPTS,
    stop_event: Event | None = None,
    check_free_space: Callable[[], None] | None = None,
    session: requests.Session | None = None,
) -> HostradaMonthDownload:
    """Download or reuse exactly three source files; never expose partial files."""
    if max_attempts < 1:
        raise ValueError("HOSTRADA download attempts must be at least one")

    started = perf_counter()
    resolved_paths = paths or HostradaPaths()
    session_context = requests.Session() if session is None else nullcontext(session)

    with session_context as active_session:
        active_session.headers.update(
            {"User-Agent": "berlin_capstone_hostrada_backfill/1.0"}
        )
        sources = tuple(
            _download_source(
                active_session,
                month=month,
                variable_name=field.variable_name,
                paths=resolved_paths,
                max_attempts=max_attempts,
                stop_event=stop_event,
                check_free_space=check_free_space,
            )
            for field in HOSTRADA_FIELD_CONTRACTS
        )

    result = HostradaMonthDownload(
        source_month=month.partition_key,
        sources=sources,
        duration_seconds=perf_counter() - started,
    )
    LOGGER.info(
        "hostrada_month_download_ready month=%s downloaded=%s reused=%s "
        "bytes=%s duration_seconds=%.2f",
        result.source_month,
        result.downloaded_file_count,
        result.reused_file_count,
        result.source_size_bytes,
        result.duration_seconds,
    )
    return result
