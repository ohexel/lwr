"""Local preview server for the Germany map.

Python's built-in server cannot answer HTTP Range requests, which PMTiles
needs; this handler serves single byte ranges.
"""

from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
import re


LOGGER = logging.getLogger(__name__)

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Return the inclusive (start, end) of a single byte range, or None if
    the header is not a satisfiable single range."""
    match = RANGE_PATTERN.match(header.strip())
    if not match or size == 0:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix range: last N bytes
        length = int(last)
        if length == 0:
            return None
        return max(size - length, 0), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start > end or start >= size:
        return None
    return start, end


class RangeRequestHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: D401 - stdlib override
        header = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if header is None or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        byte_range = parse_range(header, size)
        if byte_range is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        start, end = byte_range
        handle = path.open("rb")
        handle.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._range_remaining = end - start + 1
        return handle

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        try:
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        finally:
            self._range_remaining = None


def serve_dashboard(directory: Path, *, host: str, port: int) -> None:
    handler = partial(RangeRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    LOGGER.info("Germany map ready: http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping the local preview")
    finally:
        server.server_close()
