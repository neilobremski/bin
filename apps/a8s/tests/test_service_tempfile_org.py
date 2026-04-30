"""Tests for the TempFile.org storage service.

The fake HTTP server below mimics tempfile.org's two endpoints
(`POST /api/upload/local` and `GET /<id>/download`) so the round-trip
behavior is exercised without real network calls. A separate marker test
verifies that an upload to a deliberately-bogus URL surfaces
`StorageError`."""
from __future__ import annotations

import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services import StorageError
from services.tempfile_org import TempFileOrgService


# ---------- fake server fixture ----------


class _Handler(BaseHTTPRequestHandler):
    """Minimal in-process tempfile.org clone. Stores uploads in
    `self.server.files: dict[str, bytes]` keyed by hex id."""

    # Silence the default stdout logging.
    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.path != "/api/upload/local":
            self.send_error(404)
            return
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(.+)$", ctype)
        if not m:
            self.send_error(400, "missing boundary")
            return
        boundary = m.group(1).encode()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        # Crude multipart parse — just enough for the test fixture. Pull out
        # the `files` field's binary payload.
        parts = body.split(b"--" + boundary)
        file_bytes: bytes | None = None
        for part in parts:
            if b'name="files"' in part:
                # Skip headers (terminated by \r\n\r\n) and trailing CRLF.
                _, _, rest = part.partition(b"\r\n\r\n")
                file_bytes = rest.rsplit(b"\r\n", 1)[0]
                break
        if file_bytes is None:
            self.send_error(400, "missing files field")
            return
        file_id = f"f{len(self.server.files):04d}"  # deterministic id
        self.server.files[file_id] = file_bytes
        port = self.server.server_address[1]
        url = f"http://127.0.0.1:{port}/{file_id}/"
        body_out = json.dumps({
            "files": [{
                "id": file_id,
                "name": "x",
                "size": len(file_bytes),
                "url": url,
                "expiryTime": 0,
            }]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def do_GET(self):
        m = re.match(r"^/(?P<id>[^/]+)/download$", self.path)
        if not m:
            self.send_error(404)
            return
        file_id = m.group("id")
        if file_id not in self.server.files:
            self.send_error(404, "unknown id")
            return
        data = self.server.files[file_id]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_tempfile():
    """Spin up an in-process tempfile.org clone on a random port. Yields
    its base URL (e.g. `http://127.0.0.1:<port>`)."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.files = {}  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


# ---------- option-bag handling ----------


class TestTempFileOrgServiceOptions:
    def test_defaults(self):
        s = TempFileOrgService("t", url="https://tempfile.org")
        assert s.id == "t"
        assert s._expiry_hours == 24
        assert s._timeout_s == 30.0

    def test_custom_expiry(self):
        s = TempFileOrgService("t", url="https://tempfile.org", expiry_hours=6)
        assert s._expiry_hours == 6

    def test_string_expiry_coerced(self):
        # `network.json` values come through as strings (CLI parsing).
        s = TempFileOrgService("t", url="https://tempfile.org", expiry_hours="48")
        assert s._expiry_hours == 48

    def test_bad_expiry_raises(self):
        with pytest.raises(ValueError, match="expiry_hours must be one of"):
            TempFileOrgService("t", url="https://tempfile.org", expiry_hours=12)

    def test_unknown_option_raises(self):
        with pytest.raises(ValueError, match="unknown option"):
            TempFileOrgService("t", url="https://tempfile.org", boguskey="x")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="unsupported scheme"):
            TempFileOrgService("t", url="ftp://tempfile.org")

    def test_missing_host_raises(self):
        with pytest.raises(ValueError, match="missing host"):
            TempFileOrgService("t", url="https://")


# ---------- supports_config_url dispatch ----------


class TestSupportsConfigUrl:
    def test_canonical_host(self):
        assert TempFileOrgService.supports_config_url("https://tempfile.org") is True

    def test_path_tolerated(self):
        assert TempFileOrgService.supports_config_url("https://tempfile.org/api") is True

    def test_www_subdomain(self):
        assert TempFileOrgService.supports_config_url("https://www.tempfile.org") is True

    def test_other_host(self):
        assert TempFileOrgService.supports_config_url("https://example.com") is False

    def test_non_http_scheme(self):
        assert TempFileOrgService.supports_config_url("ftp://tempfile.org") is False


# ---------- store / retrieve round-trip ----------


class TestRoundTrip:
    def test_upload_then_download_match(self, fake_tempfile, tmp_path):
        s = TempFileOrgService("t", url=fake_tempfile)
        src = tmp_path / "payload.bin"
        src.write_bytes(b"hello payload \x00\x01\x02")
        url = s.store(src)
        assert url.startswith(fake_tempfile)
        dest = tmp_path / "downloaded.bin"
        assert s.retrieve(url, dest) is True
        assert dest.read_bytes() == src.read_bytes()


class TestRetrieveDispatch:
    def test_foreign_host_returns_false(self, fake_tempfile, tmp_path):
        s = TempFileOrgService("t", url=fake_tempfile)
        # URL belongs to a host this service isn't configured for.
        dest = tmp_path / "downloaded.bin"
        assert s.retrieve("https://other.example/abc/", dest) is False
        assert not dest.exists()

    def test_already_has_download_suffix(self, fake_tempfile, tmp_path):
        # Tolerate either the trailing-slash form (what tempfile.org returns)
        # or an explicit `/download` suffix (operator-typed for testing).
        s = TempFileOrgService("t", url=fake_tempfile)
        src = tmp_path / "payload.bin"
        src.write_bytes(b"x")
        landing_url = s.store(src)
        explicit = landing_url.rstrip("/") + "/download"
        dest = tmp_path / "downloaded.bin"
        assert s.retrieve(explicit, dest) is True
        assert dest.read_bytes() == b"x"


class TestErrorPaths:
    def test_unreachable_upload_raises(self, tmp_path):
        # No server on this port — `urlopen` raises URLError quickly.
        s = TempFileOrgService(
            "t", url=f"http://127.0.0.1:{_free_port()}", timeout_s=0.5,
        )
        src = tmp_path / "payload.bin"
        src.write_bytes(b"x")
        with pytest.raises(StorageError):
            s.store(src)

    def test_404_download_raises(self, fake_tempfile, tmp_path):
        # URL host matches the service but the file doesn't exist server-side.
        s = TempFileOrgService("t", url=fake_tempfile)
        dest = tmp_path / "downloaded.bin"
        with pytest.raises(StorageError, match="download HTTP 404"):
            s.retrieve(f"{fake_tempfile}/no-such-id/", dest)

    def test_missing_source_raises(self, fake_tempfile, tmp_path):
        s = TempFileOrgService("t", url=fake_tempfile)
        with pytest.raises(StorageError, match="cannot read"):
            s.store(tmp_path / "no-such-file.bin")
