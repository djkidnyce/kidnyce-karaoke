"""kidnyce-karaoke-netfallback with stand-ins for ip, systemctl and sleep.

Run with: python3 -m pytest deploy/tests/test_netfallback_script.py
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "files" / "kidnyce-karaoke-netfallback"

# `ip` answers from a list of states, one per call to `ip -4 -o addr`:
# "up" (a router address), "link" (169.254 only) or "down" (nothing).
FAKE_IP = r"""#!/bin/bash
state_file="$FAKE_DIR/states"
count_file="$FAKE_DIR/count"
n=$(cat "$count_file" 2>/dev/null || echo 0)
mapfile -t states <"$state_file"
idx=$(( n < ${#states[@]} ? n : ${#states[@]} - 1 ))
state=${states[$idx]}
if [[ "$*" == *"addr show"* ]]; then
    echo $((n + 1)) >"$count_file"
    case "$state" in
        up) echo "2: wlan0    inet 192.168.6.186/24 brd 192.168.6.255 scope global wlan0" ;;
        link) echo "2: wlan0    inet 169.254.10.2/16 brd 169.254.255.255 scope global wlan0" ;;
    esac
elif [[ "$*" == *"route show default"* ]]; then
    [[ $state == up ]] && echo "default via 192.168.6.1 dev wlan0"
fi
exit 0
"""

FAKE_SYSTEMCTL = """#!/bin/bash
echo "$*" >>"$FAKE_DIR/systemctl.log"
if [[ $1 == cat ]]; then [[ -n $HAVE_DM && $2 == "$HAVE_DM.service" ]]; exit $?; fi
exit 0
"""


def run(tmp_path, states, fallback="1", dropin=True, have_dm="lightdm"):
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in {
        "ip": FAKE_IP,
        "systemctl": FAKE_SYSTEMCTL,
        "sleep": "#!/bin/sh\nexit 0\n",
        "chvt": "#!/bin/sh\nexit 0\n",
    }.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    (tmp_path / "states").write_text("\n".join(states) + "\n")
    config = tmp_path / "kiosk.env"
    config.write_text(f"NET_FALLBACK={fallback}\nNET_WAIT_SECONDS=15\n")
    drop = tmp_path / "dropin.conf"
    if dropin:
        drop.write_text("[Service]\n")
    env = {
        "PATH": f"{fake}:{os.environ['PATH']}",
        "FAKE_DIR": str(tmp_path),
        "HAVE_DM": have_dm,
        "KIDNYCE_KIOSK_CONFIG": str(config),
        "KIOSK_DROPIN": str(drop),
        "CHECK_SECONDS": "5",
        "GRACE_SECONDS": "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    log = tmp_path / "systemctl.log"
    calls = (
        [c for c in log.read_text().splitlines() if not c.startswith("cat ")]
        if log.exists()
        else []
    )
    return result, calls


def test_off_does_nothing(tmp_path):
    result, calls = run(tmp_path, ["down"], fallback="0")
    assert result.returncode == 0
    assert calls == []


def test_network_up_leaves_the_screens_alone(tmp_path):
    result, calls = run(tmp_path, ["up"])
    assert result.returncode == 0
    assert calls == []
    assert "screens start as usual" in result.stderr


def test_no_network_shows_the_desktop_then_the_screens(tmp_path):
    # Three checks with nothing during the wait, then the host joins WiFi.
    result, calls = run(tmp_path, ["down", "down", "down", "down", "up", "up"])
    assert result.returncode == 0, result.stderr
    assert calls == [
        "stop getty@tty1.service",
        "start lightdm.service",
        "stop lightdm.service",
        "restart getty@tty1.service",
    ]


def test_a_self_assigned_address_is_no_network(tmp_path):
    result, calls = run(tmp_path, ["link", "link", "link", "link", "up", "up"])
    assert calls[:2] == ["stop getty@tty1.service", "start lightdm.service"]


def test_a_flapping_link_needs_two_good_checks(tmp_path):
    result, calls = run(tmp_path, ["down", "down", "down", "down", "up", "down", "up", "up"])
    assert calls[-2:] == ["stop lightdm.service", "restart getty@tty1.service"]
    count = int((tmp_path / "count").read_text())
    assert count >= 8


def test_lite_without_a_desktop_keeps_the_screens(tmp_path):
    result, calls = run(tmp_path, ["down"], have_dm="")
    assert result.returncode == 0
    assert calls == []
    assert "no desktop installed" in result.stderr


def test_desktop_mode_needs_nothing(tmp_path):
    result, calls = run(tmp_path, ["down"], dropin=False)
    assert calls == []
