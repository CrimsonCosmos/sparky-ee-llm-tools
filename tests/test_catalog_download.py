"""ensure_db against a local server that compresses whenever the client allows it (as the real host does): the download must complete and match byte for byte."""
import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sparky.picker.catalog import ensure_db

BODY = bytes(range(256)) * 4000


class _H(BaseHTTPRequestHandler):
    def _send(self, head_only):
        gz = "gzip" in self.headers.get("Accept-Encoding", "")
        body = gzip.compress(BODY) if gz else BODY
        start = 0
        rng = self.headers.get("Range")
        self.send_response(206 if rng else 200)
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            body = body[start:]
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        self._send(False)

    def do_HEAD(self):
        self._send(True)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}/db"
    s.shutdown()


def test_download_completes_when_the_host_would_compress(tmp_path, server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    p = ensure_db(tmp_path / "db", url=server)
    assert p.read_bytes() == BODY


def test_resume_appends_the_missing_tail(tmp_path, server, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (tmp_path / "db").write_bytes(BODY[:1000])
    assert ensure_db(tmp_path / "db", url=server).read_bytes() == BODY
