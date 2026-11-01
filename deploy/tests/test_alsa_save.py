"""kidnyce-karaoke-alsa-save with a stand-in alsactl.

Run with: python3 -m pytest deploy/tests/test_alsa_save.py
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "files" / "kidnyce-karaoke-alsa-save"


def run(tmp_path, levels):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    # alsactl store -f FILE writes the current levels to FILE.
    (bin_dir / "alsactl").write_text(f'#!/bin/sh\nprintf "%s" "{levels}" >"$3"\n')
    (bin_dir / "alsactl").chmod(0o755)
    state = tmp_path / "lib" / "asound.state"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ALSA_STATE": str(state),
        "ALSA_TMP": str(tmp_path / "tmp.state"),
    }
    subprocess.run(["sh", str(SCRIPT)], env=env, check=True, timeout=30)
    return state


def test_saves_the_levels(tmp_path):
    state = run(tmp_path, "PCM 80%")
    assert state.read_text() == "PCM 80%"


def test_no_write_when_nothing_changed(tmp_path):
    state = run(tmp_path, "PCM 80%")
    before = state.stat().st_mtime_ns
    os.utime(state, ns=(before - 10**9, before - 10**9))
    run(tmp_path, "PCM 80%")
    assert state.stat().st_mtime_ns == before - 10**9


def test_a_change_is_saved(tmp_path):
    run(tmp_path, "PCM 80%")
    state = run(tmp_path, "PCM 55%")
    assert state.read_text() == "PCM 55%"
