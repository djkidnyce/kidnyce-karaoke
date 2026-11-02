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


WATCH = Path(__file__).resolve().parents[1] / "files" / "kidnyce-karaoke-alsa-watch"


def watch(tmp_path, events, wait=2, timeout=30):
    """Run the watcher against a stand-in `alsactl monitor`.

    `events` is a shell snippet that prints mixer events (and sleeps between
    them); when it ends, alsactl "goes away". Returns the save times, in
    seconds from the start, and the watcher's exit code.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "alsactl").write_text(f"#!/bin/bash\n[[ $1 == monitor ]] || exit 1\n{events}\n")
    (bin_dir / "alsactl").chmod(0o755)
    log = tmp_path / "saves"
    start = tmp_path / "start"
    (bin_dir / "save").write_text(
        f"#!/bin/bash\necho $(( $(date +%s%N) / 1000000 - $(cat {start}) )) >>{log}\n"
    )
    (bin_dir / "save").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ALSA_SAVE_AFTER_SECONDS": str(wait),
        "ALSA_SAVE_CMD": str(bin_dir / "save"),
    }
    import time

    start.write_text(str(time.time_ns() // 1_000_000))
    result = subprocess.run(["bash", str(WATCH)], env=env, timeout=timeout)
    saves = [int(x) / 1000 for x in log.read_text().split()] if log.exists() else []
    return saves, result.returncode


def test_nothing_changes_nothing_saved(tmp_path):
    saves, code = watch(tmp_path, "sleep 3")
    assert saves == []
    assert code == 1  # alsactl went away; systemd restarts the watcher


def test_a_burst_of_changes_is_saved_once_after_the_wait(tmp_path):
    # Five changes over a second (a slider being dragged), then quiet.
    events = 'for i in 1 2 3 4 5; do echo "node hw:0, #1 (2,0,0,PCM Playback Volume,0) VALUE"; sleep 0.2; done; sleep 4'
    saves, _ = watch(tmp_path, events, wait=2)
    assert len(saves) == 1
    assert 1.8 <= saves[0] <= 3.5


def test_changes_later_on_are_saved_again(tmp_path):
    events = "echo change; sleep 3; echo change; sleep 3"
    saves, _ = watch(tmp_path, events, wait=1)
    assert len(saves) == 2


def test_a_pending_change_is_saved_if_alsactl_goes_away(tmp_path):
    saves, code = watch(tmp_path, "echo change; sleep 0.3", wait=5)
    assert len(saves) == 1 and saves[0] < 2
    assert code == 1
