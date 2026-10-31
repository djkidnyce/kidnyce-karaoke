"""The console menu after the screens are closed, and root's desktop helper.

Run with: python3 -m pytest deploy/tests/test_screens_closed.py
Stand-ins replace systemctl, curl, nmtui, clear and sleep.
"""

import json
import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
MENU = DEPLOY / "kiosk" / "screens-closed.sh"
HELPER = DEPLOY / "files" / "kidnyce-karaoke-desktop-now"
ME = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()


def fake_bin(tmp_path, have_dm=True, password="right horse"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    scripts = {
        "systemctl": f"""#!/bin/bash
echo "$*" >>"{tmp_path}/systemctl.log"
if [[ $1 == cat ]]; then [[ {1 if have_dm else 0} == 1 && $2 == lightdm.service ]]; exit $?; fi
exit 0
""",
        # Stands in for the app's /api/auth: reads the JSON body from stdin.
        "curl": f"""#!/bin/bash
body=$(cat)
echo "$body" >>"{tmp_path}/curl.log"
if [[ $(jq -r .admin_password <<<"$body") == "{password}" ]]; then echo '{{"authenticated": true}}'; else exit 22; fi
""",
        "nmtui": f"#!/bin/sh\necho nmtui >>{tmp_path}/nmtui.log\n",
        "clear": "#!/bin/sh\nexit 0\n",
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    for name, body in scripts.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


def env(tmp_path, bin_dir, **extra):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "KIOSK_REQUEST_DIR": str(run_dir),
        "REQUEST_DIR": str(run_dir),
        "ETC_DIR": str(tmp_path / "etc"),
        "KIOSK_USER": ME,
        "HOME": str(tmp_path),
        **extra,
    }


def log(tmp_path, name):
    f = tmp_path / name
    return f.read_text().splitlines() if f.exists() else []


class TestMenu:
    def run(self, tmp_path, keys, **kw):
        bin_dir = fake_bin(tmp_path, **kw)
        return subprocess.run(
            ["bash", str(MENU)],
            input=keys,
            env=env(tmp_path, bin_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_enter_starts_the_screens_again(self, tmp_path):
        result = self.run(tmp_path, "\n")
        assert result.returncode == 0
        assert "screens closed" in result.stdout
        assert "D       show the desktop" in result.stdout

    def test_no_desktop_option_on_lite(self, tmp_path):
        result = self.run(tmp_path, "\n", have_dm=False)
        assert "show the desktop" not in result.stdout

    def test_w_opens_the_wifi_menu_then_returns(self, tmp_path):
        result = self.run(tmp_path, "w\n")
        assert result.returncode == 0
        assert log(tmp_path, "nmtui.log") == ["nmtui"]

    def test_d_hands_the_password_to_root_privately(self, tmp_path):
        # Nobody answers here, so the menu gives up and shows itself again.
        result = self.run(tmp_path, "dsecret\n\n")
        request = tmp_path / "run" / "desktop-request"
        assert request.read_text() == "secret\n"
        assert oct(request.stat().st_mode & 0o777) == "0o600"
        assert "secret" not in result.stdout

    def test_no_key_for_a_while_starts_the_screens(self, tmp_path):
        bin_dir = fake_bin(tmp_path)
        result = subprocess.run(
            ["bash", "-c", f"sleep 5 | IDLE_SECONDS=1 bash {MENU}"],
            env=env(tmp_path, bin_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0


class TestDesktopHelper:
    def request(self, tmp_path, password):
        run_dir = tmp_path / "run"
        run_dir.mkdir(exist_ok=True)
        f = run_dir / "desktop-request"
        f.write_text(password + "\n")
        f.chmod(0o600)

    def run(self, tmp_path, **kw):
        bin_dir = fake_bin(tmp_path, **kw)
        result = subprocess.run(
            ["bash", str(HELPER)],
            env=env(tmp_path, bin_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        answer = tmp_path / "run" / "desktop-result"
        return result, (answer.read_text().strip() if answer.exists() else None)

    def calls(self, tmp_path):
        return [c for c in log(tmp_path, "systemctl.log") if not c.startswith("cat ")]

    def test_right_password_shows_the_desktop(self, tmp_path):
        self.request(tmp_path, "right horse")
        result, answer = self.run(tmp_path)
        assert answer == "ok", result.stderr
        assert self.calls(tmp_path) == ["stop getty@tty1.service", "start lightdm.service"]
        assert not (tmp_path / "run" / "desktop-request").exists()
        assert json.loads((tmp_path / "curl.log").read_text()) == {"admin_password": "right horse"}

    def test_wrong_password_changes_nothing(self, tmp_path):
        self.request(tmp_path, "guess")
        _, answer = self.run(tmp_path)
        assert answer == "denied"
        assert self.calls(tmp_path) == []
        assert not (tmp_path / "run" / "desktop-request").exists()

    def test_lite_says_there_is_no_desktop(self, tmp_path):
        self.request(tmp_path, "right horse")
        _, answer = self.run(tmp_path, have_dm=False)
        assert answer == "none"
        assert self.calls(tmp_path) == []

    def test_a_link_is_never_followed(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (tmp_path / "elsewhere").write_text("right horse\n")
        (run_dir / "desktop-request").symlink_to(tmp_path / "elsewhere")
        _, answer = self.run(tmp_path)
        assert answer is None
        assert self.calls(tmp_path) == []
        assert not (run_dir / "desktop-request").exists()

    def test_a_request_from_someone_else_is_ignored(self, tmp_path):
        self.request(tmp_path, "right horse")
        bin_dir = fake_bin(tmp_path)
        result = subprocess.run(
            ["bash", str(HELPER)],
            env=env(tmp_path, bin_dir, KIOSK_USER="kiosk-not-me"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "ignored a request" in result.stderr
        assert self.calls(tmp_path) == []
