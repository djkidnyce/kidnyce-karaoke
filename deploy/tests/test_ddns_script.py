"""kidnyce-karaoke-ddns against a mock Cloudflare API.

Run with: python3 -m pytest deploy/tests/test_ddns_script.py
Needs bash, curl, jq and iproute2 (the script's own dependencies).
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "files" / "kidnyce-karaoke-ddns"
TOKEN = "test-token-0123456789abcdef0123456789abcdef"
ZONE_ID = "zone123"


class MockCloudflare(BaseHTTPRequestHandler):
    records: list[dict] = []
    calls: list[tuple[str, str, dict | None, str]] = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _record(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.calls.append((method, self.path, body, self.headers.get("Authorization", "")))
        return body

    def do_GET(self):
        self._record("GET")
        if not self._authorized():
            return self._reply({"success": False, "errors": [{"code": 10000}]}, 403)
        if self.path.startswith("/client/v4/zones?name="):
            return self._reply({"success": True, "result": [{"id": ZONE_ID}]})
        if self.path.startswith(f"/client/v4/zones/{ZONE_ID}/dns_records"):
            return self._reply({"success": True, "result": self.records})
        return self._reply({"success": False}, 404)

    def do_POST(self):
        body = self._record("POST")
        self.records.append({"id": "rec1", **body})
        self._reply({"success": True, "result": self.records[-1]})

    def do_PUT(self):
        body = self._record("PUT")
        self.records[0].update(body)
        self._reply({"success": True, "result": self.records[0]})


@pytest.fixture
def api():
    MockCloudflare.records = []
    MockCloudflare.calls = []
    server = HTTPServer(("127.0.0.1", 0), MockCloudflare)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/client/v4"
    server.shutdown()


def run(tmp_path: Path, api_base: str, ip: str, **extra) -> subprocess.CompletedProcess:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    token_file.chmod(0o600)
    config = tmp_path / "ddns.env"
    config.write_text(
        f"DOMAIN=karaoke.djkidnyce.com\nZONE=djkidnyce.com\nTOKEN_FILE={token_file}\n"
        f"STATE_DIR={tmp_path / 'state'}\nCF_API_BASE={api_base}\n"
        + "".join(f"{k}={v}\n" for k, v in extra.items())
    )
    # Stand in for `ip`, so the test controls the "LAN address".
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "ip").write_text(
        f"#!/bin/sh\necho '1.1.1.1 via 10.0.0.1 dev wlan0 src {ip} uid 0'\n"
    )
    (fake_bin / "ip").chmod(0o755)
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "KIDNYCE_DDNS_CONFIG": str(config),
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )


def test_creates_the_record_when_missing(tmp_path, api):
    result = run(tmp_path, api, "192.168.1.50")
    assert result.returncode == 0, result.stderr
    posts = [c for c in MockCloudflare.calls if c[0] == "POST"]
    assert len(posts) == 1
    body = posts[0][2]
    assert body["type"] == "A"
    assert body["name"] == "karaoke.djkidnyce.com"
    assert body["content"] == "192.168.1.50"
    assert body["proxied"] is False
    assert body["ttl"] == 60
    assert all(c[3] == f"Bearer {TOKEN}" for c in MockCloudflare.calls)


def test_updates_when_the_pi_moves_to_another_venue(tmp_path, api):
    MockCloudflare.records = [{"id": "rec1", "type": "A", "content": "192.168.1.50"}]
    result = run(tmp_path, api, "10.20.30.40")
    assert result.returncode == 0, result.stderr
    puts = [c for c in MockCloudflare.calls if c[0] == "PUT"]
    assert len(puts) == 1 and puts[0][1].endswith("/dns_records/rec1")
    assert MockCloudflare.records[0]["content"] == "10.20.30.40"


def test_does_nothing_when_already_right(tmp_path, api):
    MockCloudflare.records = [{"id": "rec1", "type": "A", "content": "192.168.1.50"}]
    result = run(tmp_path, api, "192.168.1.50")
    assert result.returncode == 0, result.stderr
    assert not [c for c in MockCloudflare.calls if c[0] in ("POST", "PUT")]


def test_skips_the_api_when_nothing_changed_recently(tmp_path, api):
    run(tmp_path, api, "192.168.1.50")
    MockCloudflare.calls.clear()
    result = run(tmp_path, api, "192.168.1.50")
    assert result.returncode == 0
    assert MockCloudflare.calls == []


def test_refuses_to_publish_a_public_address(tmp_path, api):
    result = run(tmp_path, api, "8.8.8.8")
    assert result.returncode == 1
    assert "refusing" in result.stderr
    assert MockCloudflare.calls == []


def test_uses_a_given_zone_id_without_listing_zones(tmp_path, api):
    result = run(tmp_path, api, "192.168.1.50", ZONE_ID=ZONE_ID)
    assert result.returncode == 0, result.stderr
    assert not [c for c in MockCloudflare.calls if "zones?name=" in c[1]]


def test_token_never_appears_on_a_command_line():
    """The token reaches curl through --config on stdin, not argv."""
    script = SCRIPT.read_text()
    assert "--config -" in script
    assert "Bearer $token" not in script.replace('printf \'header = "Authorization: Bearer %s"', "")
