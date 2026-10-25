"""kidnyce-karaoke-tunnel against a mock Cloudflare API.

Run with: python3 -m pytest deploy/tests/test_tunnel_script.py
Needs bash, curl and jq (the script's own dependencies).
"""

import os
import subprocess
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_cloudflare import TOKEN, MockCloudflare  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "files" / "kidnyce-karaoke-tunnel"
HOST = "karaoke-test.kidnyce.com"


@pytest.fixture
def api():
    MockCloudflare.tunnels = []
    MockCloudflare.records = []
    MockCloudflare.config = None
    MockCloudflare.calls = []
    server = HTTPServer(("127.0.0.1", 0), MockCloudflare)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/client/v4"
    server.shutdown()


def run(tmp_path: Path, api_base: str, command: str, token: str = TOKEN):
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n")
    config = tmp_path / "tunnel.env"
    config.write_text(
        f"HOSTNAME_PUBLIC={HOST}\nZONE=kidnyce.com\nTOKEN_FILE={token_file}\n"
        f"TUNNEL_NAME=kidnyce-karaoke-karaoke-test\nORIGIN=http://127.0.0.1:8081\n"
        f"TUNNEL_TOKEN_FILE={tmp_path / 'tunnel.token'}\nCF_API_BASE={api_base}\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "KIDNYCE_TUNNEL_CONFIG": str(config),
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), command], env=env, capture_output=True, text=True, timeout=30
    )


def test_setup_creates_tunnel_route_record_and_token(tmp_path, api):
    result = run(tmp_path, api, "setup")
    assert result.returncode == 0, result.stderr
    assert MockCloudflare.tunnels == [{"id": "tun1", "name": "kidnyce-karaoke-karaoke-test"}]
    create = next(c for c in MockCloudflare.calls if c[0] == "POST" and c[1].endswith("cfd_tunnel"))
    assert create[2]["config_src"] == "cloudflare"
    config = MockCloudflare.config or {}
    ingress = config["config"]["ingress"]
    assert ingress[0] == {"hostname": HOST, "service": "http://127.0.0.1:8081", "originRequest": {}}
    assert ingress[-1] == {"service": "http_status:404"}
    (record,) = MockCloudflare.records
    assert record["type"] == "CNAME" and record["name"] == HOST
    assert record["content"] == "tun1.cfargotunnel.com" and record["proxied"] is True
    token = tmp_path / "tunnel.token"
    assert token.read_text().strip() == "tunnel-token-for-tun1"
    assert oct(token.stat().st_mode & 0o777) == "0o600"
    assert TOKEN not in result.stderr


def test_setup_twice_reuses_everything(tmp_path, api):
    assert run(tmp_path, api, "setup").returncode == 0
    result = run(tmp_path, api, "setup")
    assert result.returncode == 0, result.stderr
    assert len(MockCloudflare.tunnels) == 1
    assert len(MockCloudflare.records) == 1
    assert "reusing tunnel" in result.stderr


def test_never_replaces_someone_elses_record(tmp_path, api):
    MockCloudflare.records = [{"id": "lan", "type": "A", "name": HOST, "content": "192.168.6.186"}]
    result = run(tmp_path, api, "setup")
    assert result.returncode != 0
    assert "already has other DNS records" in result.stderr
    assert MockCloudflare.records == [
        {"id": "lan", "type": "A", "name": HOST, "content": "192.168.6.186"}
    ]
    assert not (tmp_path / "tunnel.token").exists()


def test_status_reports_the_connections(tmp_path, api):
    run(tmp_path, api, "setup")
    result = run(tmp_path, api, "status")
    assert result.returncode == 0, result.stderr
    assert "healthy, 4 connection(s)" in result.stdout


def test_remove_deletes_only_our_record_and_the_tunnel(tmp_path, api):
    run(tmp_path, api, "setup")
    MockCloudflare.records.append(
        {"id": "other", "type": "CNAME", "name": HOST, "content": "elsewhere.example.com"}
    )
    result = run(tmp_path, api, "remove")
    assert result.returncode == 0, result.stderr
    assert [r["id"] for r in MockCloudflare.records] == ["other"]
    assert MockCloudflare.tunnels == []
    assert not (tmp_path / "tunnel.token").exists()


def test_a_token_without_tunnel_rights_says_so(tmp_path, api):
    result = run(tmp_path, api, "setup", token="wrong-token-0123456789")
    assert result.returncode != 0
