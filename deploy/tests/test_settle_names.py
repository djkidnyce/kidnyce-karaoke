"""The installer's first questions: domain from the token's zones, guests, host name.

Sources deploy/install-pi.sh (which defines everything without running) and calls
settle_names against the mock Cloudflare API, with the answers scripted.
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

INSTALLER = Path(__file__).resolve().parents[1] / "install-pi.sh"


@pytest.fixture
def api():
    server = HTTPServer(("127.0.0.1", 0), MockCloudflare)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/client/v4"
    server.shutdown()


def settle(tmp_path, api, answers=(), setup="", saved=None):
    """Run settle_names; return the variables it decided on."""
    etc = tmp_path / "etc"
    etc.mkdir(exist_ok=True)
    if saved is not None:
        (etc / "install.env").write_text(saved)
    # One answer per line; ask runs in a subshell, so the queue lives in a file.
    queue = tmp_path / "answers"
    queue.write_text("".join(f"{a}\n" for a in answers))
    script = f"""
source '{INSTALLER}'
ETC_DIR='{etc}'
CF_TOKEN='{TOKEN}'
CF_API_BASE='{api}'
ask() {{ local r; r=$(head -n 1 '{queue}'); sed -i 1d '{queue}'; printf '%s\\n' "${{r:-$2}}"; }}
{setup}
settle_names >&2
for v in DOMAIN ZONE GUESTS HOST_NAME VENUE_NAME TUNNEL_HOSTNAME DDNS HOST_OPEN_URL; do
    printf '%s=%s\\n' "$v" "${{!v}}"
done
"""
    env = {"PATH": os.environ["PATH"], "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    result = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result, values


def test_first_install_picks_the_zone_from_the_token(tmp_path, api):
    result, v = settle(tmp_path, api, answers=["", "1", ""])
    assert result.returncode == 0, result.stderr
    assert v["DOMAIN"] == "karaoke.example.com"
    assert v["ZONE"] == "example.com"
    assert v["GUESTS"] == "tunnel"
    assert v["TUNNEL_HOSTNAME"] == "karaoke.example.com"
    assert v["HOST_NAME"] == "host.karaoke.example.com"
    assert v["VENUE_NAME"] == "host.karaoke.example.com"
    assert v["HOST_OPEN_URL"] == "https://host.karaoke.example.com/"


def test_no_host_name_means_the_pi_itself(tmp_path, api):
    result, v = settle(tmp_path, api, answers=["sing", "1", "none"])
    assert result.returncode == 0, result.stderr
    assert v["DOMAIN"] == "sing.example.com"
    assert v["HOST_NAME"] == ""
    assert v["VENUE_NAME"] == ""
    assert v["DDNS"] == "0"
    assert v["HOST_OPEN_URL"].startswith("http://127.0.0.1:")


def test_venue_network_only(tmp_path, api):
    result, v = settle(tmp_path, api, answers=["", "2"])
    assert result.returncode == 0, result.stderr
    assert v["GUESTS"] == "venue"
    assert v["VENUE_NAME"] == "karaoke.example.com"
    assert v["TUNNEL_HOSTNAME"] == ""


def test_a_rerun_keeps_the_answers_and_asks_nothing(tmp_path, api):
    saved = "DOMAIN=karaoke.kidnyce.com\nZONE=kidnyce.com\nGUESTS=tunnel\nHOST_NAME=host.karaoke.kidnyce.com\n"
    result, v = settle(tmp_path, api, answers=["wrong", "wrong", "wrong"], saved=saved)
    assert result.returncode == 0, result.stderr
    assert v["DOMAIN"] == "karaoke.kidnyce.com"
    assert v["GUESTS"] == "tunnel"
    assert v["VENUE_NAME"] == "host.karaoke.kidnyce.com"


def test_an_old_venue_install_moves_to_the_tunnel_and_is_asked_for_a_host_name(tmp_path, api):
    saved = "DOMAIN=karaoke.kidnyce.com\nZONE=kidnyce.com\n"
    result, v = settle(tmp_path, api, answers=[""], setup="GUESTS=tunnel", saved=saved)
    assert result.returncode == 0, result.stderr
    assert v["TUNNEL_HOSTNAME"] == "karaoke.kidnyce.com"
    assert v["HOST_NAME"] == "host.karaoke.kidnyce.com"


def test_yes_without_a_domain_stops_and_says_so(tmp_path, api):
    result, _ = settle(tmp_path, api, setup="ASSUME_YES=1")
    assert result.returncode != 0
    assert "--domain is needed" in result.stderr


def test_yes_defaults_to_the_venue_network(tmp_path, api):
    result, v = settle(
        tmp_path, api, setup="ASSUME_YES=1; DOMAIN=karaoke.example.com; DOMAIN_SET=1"
    )
    assert result.returncode == 0, result.stderr
    assert v["GUESTS"] == "venue"


@pytest.mark.parametrize(
    "setup,message",
    [
        ("GUESTS=tunnel; HOST_NAME=host.other.org; HOST_NAME_SET=1", "inside the zone"),
        (
            "GUESTS=tunnel; TUNNEL_HOSTNAME=karaoke-test.example.com",
            "--tunnel is for --guests venue",
        ),
        ("GUESTS=venue; TUNNEL_HOSTNAME=karaoke.example.com", "use --guests tunnel"),
    ],
)
def test_contradictions_are_refused(tmp_path, api, setup, message):
    result, _ = settle(
        tmp_path,
        api,
        setup=f"ASSUME_YES=1; DOMAIN=karaoke.example.com; DOMAIN_SET=1; {setup}",
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_local_only_needs_no_domain(tmp_path, api):
    result, v = settle(tmp_path, api, setup="DNS_PROVIDER=none; CF_TOKEN=")
    assert result.returncode == 0, result.stderr
    assert v["VENUE_NAME"] == ""
    assert v["TUNNEL_HOSTNAME"] == ""
    assert v["DOMAIN"].endswith(".local")
