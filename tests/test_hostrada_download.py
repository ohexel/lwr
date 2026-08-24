from pathlib import Path
from threading import Event

import pytest
import requests

from src.hostrada_contract import HOSTRADA_REQUIRED_VARIABLES, HostradaMonthKey
from src.hostrada_paths import HostradaPaths
from src.ingestion import hostrada_download
from src.ingestion.hostrada_download import download_hostrada_month


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status_code: int = 200,
        interrupted: bool = False,
    ):
        self.content = content
        self.status_code = status_code
        self.interrupted = interrupted
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )

    def iter_content(self, chunk_size):
        del chunk_size
        midpoint = max(1, len(self.content) // 2)
        yield self.content[:midpoint]
        if self.interrupted:
            raise requests.ConnectionError("interrupted transfer")
        yield self.content[midpoint:]


class FakeSession:
    def __init__(self, responses=None):
        self.headers = {}
        self.responses = responses or {}
        self.requested_urls = []

    def get(self, url, **kwargs):
        del kwargs
        self.requested_urls.append(url)
        variable = Path(url).name.split("_", 1)[0]
        choices = self.responses.get(variable)
        if choices:
            return choices.pop(0)
        return FakeResponse(f"complete-{variable}".encode())


def test_hostrada_download_streams_three_sources_and_reuses_complete_files(
    tmp_path: Path,
):
    month = HostradaMonthKey(1995, 1)
    paths = HostradaPaths(tmp_path)
    session = FakeSession()

    first = download_hostrada_month(month, paths, session=session)
    second = download_hostrada_month(month, paths, session=session)

    assert first.downloaded_file_count == 3
    assert first.reused_file_count == 0
    assert second.downloaded_file_count == 0
    assert second.reused_file_count == 3
    assert len(session.requested_urls) == 3
    assert not list(tmp_path.rglob("*.part"))
    for variable in HOSTRADA_REQUIRED_VARIABLES:
        assert paths.source_file(month, variable).read_bytes() == (
            f"complete-{variable}".encode()
        )


def test_hostrada_download_retries_interrupted_transfer_atomically(
    tmp_path: Path,
    monkeypatch,
):
    month = HostradaMonthKey(1995, 1)
    paths = HostradaPaths(tmp_path)
    session = FakeSession(
        {
            "tas": [
                FakeResponse(b"complete-tas", interrupted=True),
                FakeResponse(b"complete-tas"),
            ]
        }
    )
    monkeypatch.setattr(hostrada_download, "DEFAULT_RETRY_DELAY_SECONDS", 0.0)

    result = download_hostrada_month(month, paths, session=session)

    assert result.sources[0].attempts == 2
    assert paths.source_file(month, "tas").read_bytes() == b"complete-tas"
    assert not list(tmp_path.rglob("*.part"))


def test_hostrada_download_does_not_retry_missing_source(
    tmp_path: Path,
):
    month = HostradaMonthKey(1995, 1)
    paths = HostradaPaths(tmp_path)
    session = FakeSession(
        {"tas": [FakeResponse(b"missing", status_code=404)]}
    )

    with pytest.raises(requests.HTTPError, match="404"):
        download_hostrada_month(month, paths, session=session)

    assert len(session.requested_urls) == 1
    assert not paths.source_file(month, "tas").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_hostrada_download_honors_cancellation_before_creating_files(
    tmp_path: Path,
):
    month = HostradaMonthKey(1995, 1)
    stop_event = Event()
    stop_event.set()

    with pytest.raises(RuntimeError, match="cancelled"):
        download_hostrada_month(
            month,
            HostradaPaths(tmp_path),
            session=FakeSession(),
            stop_event=stop_event,
        )

    assert not list(tmp_path.rglob("*.nc"))
