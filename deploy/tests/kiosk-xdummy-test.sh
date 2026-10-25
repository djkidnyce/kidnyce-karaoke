#!/usr/bin/env bash
# Tests deploy/kiosk/kiosk-session.sh against a real Xorg server with virtual
# outputs (xserver-xorg-video-dummy) standing in for the Pi's two HDMI ports.
# Needs the installed app running on 127.0.0.1:5555 (sandbox-e2e.sh runs this
# at its end), Xorg with the dummy driver, xrandr, xwininfo, xdotool, and a
# Chromium binary.
#
#   sudo CHROME=/path/to/chrome deploy/tests/kiosk-xdummy-test.sh
#
# Checks: with two TVs Auto gives a singer and a room screen side by side at the
# requested mode, each browser on the right port with the right page; a crashed
# browser comes back; the app sees the ports; the host switches to one screen
# for everything, moves it to the other port, and goes back to two screens
# swapped, each within seconds and without a restart; with one TV everything
# goes on it.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION="$HERE/../kiosk/kiosk-session.sh"
CHROME=${CHROME:?set CHROME to a Chromium binary}
CLI=${CLI:-/usr/local/bin/kidnyce-karaoke}
APP=http://127.0.0.1:5555
WORK=$(mktemp -d)
PASS=0
FAIL=0
X_PID=""
S_PID=""
export NO_PROXY="127.0.0.1${NO_PROXY:+,$NO_PROXY}" no_proxy="127.0.0.1${no_proxy:+,$no_proxy}"

pass() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$*"; }
check() { local name=$1; shift; if "$@" >/dev/null; then pass "$name"; else fail "$name"; fi; }

cat >"$WORK/xorg.conf" <<'EOF'
Section "Device"
    Identifier "dummy"
    Driver "dummy"
    VideoRam 512000
EndSection
Section "Monitor"
    Identifier "m"
    HorizSync 5.0-1000.0
    VertRefresh 5.0-200.0
EndSection
Section "Screen"
    Identifier "s"
    Device "dummy"
    Monitor "m"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual 3840 2160
    EndSubSection
EndSection
EOF

mkdir -p "$WORK/bin" "$WORK/home"
# Root cannot run Chromium's sandbox; the real kiosk runs as an unprivileged user.
printf '#!/bin/bash\nexec "%s" --no-sandbox "$@"\n' "$CHROME" >"$WORK/bin/chromium"
chmod +x "$WORK/bin/chromium"
cat >"$WORK/kiosk.env" <<EOF
APP_URL=$APP
DISPLAY_MODE=1920x1080
AUDIO_OUT=default
POLL_SECONDS=2
EOF

start_x() {
    Xorg ":$1" -config "$WORK/xorg.conf" -noreset -nolisten tcp -logfile "$WORK/xorg-$1.log" >/dev/null 2>&1 &
    X_PID=$!
    sleep 3
}

start_session() {
    # Keep every run's log for a failure report.
    [[ -f $WORK/kiosk.log ]] && cat "$WORK/kiosk.log" >>"$WORK/kiosk-all.log"
    : >"$WORK/kiosk.log"
    # exec, so S_PID is the session itself and stop_all really stops it.
    (exec_session ":$1" "${@:2}") &
    S_PID=$!
}

# The session script with this test's settings, in place of the calling
# shell. DISPLAY first, then its flags.
exec_session() {
    mkdir -p "$WORK/run"
    exec env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
        PATH="$WORK/bin:$PATH" HOME="$WORK/home" KIDNYCE_KIOSK_CONFIG="$WORK/kiosk.env" \
        XDG_RUNTIME_DIR="$WORK/run" KIOSK_LOG="$WORK/kiosk.log" DISPLAY="$1" "$SESSION" "${@:2}" >/dev/null 2>&1
}

# One run that waits for the script to finish (a second click, --stop).
session() { (exec_session "$@"); }

stop_all() {
    [[ -n $S_PID ]] && kill "$S_PID" 2>/dev/null
    local pid
    for pid in $(pgrep -f "^bash $SESSION"); do kill "$pid" 2>/dev/null; done
    sleep 1
    pkill -x chrome 2>/dev/null
    [[ -n $X_PID ]] && kill "$X_PID" 2>/dev/null
    sleep 1
    S_PID=""
    X_PID=""
}

host_defaults() {
    "$CLI" screens auto >/dev/null && "$CLI" screens swap off >/dev/null && "$CLI" screens port auto >/dev/null &&
        "$CLI" audio reset >/dev/null && "$CLI" audio volume keep >/dev/null &&
        "$CLI" desktop-autostart off >/dev/null
}
trap 'stop_all; host_defaults' EXIT

# Window geometry as WxH+X+Y for every visible top level Chromium window.
windows() {
    local id
    for id in $(DISPLAY=":$1" xdotool search --onlyvisible --name 'PiKaraoke' 2>/dev/null); do
        DISPLAY=":$1" xwininfo -id "$id" | awk '/Absolute upper-left X/ {x = $NF} /Absolute upper-left Y/ {y = $NF}
            /^  Width/ {w = $NF} /^  Height/ {h = $NF} END {print w "x" h "+" x "+" y}'
    done | sort
}

# "DUMMY0 1920x1080+0+0" per connected output; one that is off says "off".
outputs() {
    DISPLAY=":$1" xrandr --query | awk '$2 == "connected" {g = "off"
        for (i = 3; i <= NF; i++) if ($i ~ /^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$/) g = $i
        print $1, g}'
}

# Wait until the kiosk log holds a line at least COUNT times.
wait_log() {
    local line=$1 count=${2:-1} _
    for _ in $(seq 1 40); do
        (($(grep -cF -- "$line" "$WORK/kiosk.log") >= count)) && return 0
        sleep 1
    done
    return 1
}

started() { grep -cF "started $1 screen" "$WORK/kiosk.log"; }

host_defaults || { echo "cannot reach the app as host through $CLI"; exit 1; }

echo "== Two TVs, Auto"
start_x 91
# Plug in the second "TV": give DUMMY1 a mode, at an odd size and place the
# session has to correct.
DISPLAY=:91 xrandr --addmode DUMMY1 1920x1080
DISPLAY=:91 xrandr --addmode DUMMY1 1280x1024
DISPLAY=:91 xrandr --output DUMMY0 --mode 1600x1200 --output DUMMY1 --mode 1280x1024 --pos 1600x0
start_session 91
wait_log "started crowd screen"
sleep 3
layout=$(outputs 91)
if grep -qx "DUMMY0 1920x1080+0+0" <<<"$layout" && grep -qx "DUMMY1 1920x1080+1920+0" <<<"$layout"; then
    pass "outputs set to 1920x1080, side by side"
else
    fail "layout: $layout"
fi
check "singer screen on DUMMY0 without the QR code" \
    grep -F "started singer screen at 1920x1080+0+0: $APP/splash?singer=1" "$WORK/kiosk.log"
check "room screen on DUMMY1" grep -F "started crowd screen at 1920x1080+1920+0: $APP/crowd" "$WORK/kiosk.log"
geoms=$(windows 91 | tr '\n' ' ')
# Without a window manager a borderless window can report 1px under its request.
if [[ $geoms =~ (1919|1920)x(1079|1080)\+0\+0 && $geoms =~ (1919|1920)x(1079|1080)\+1920\+0 ]]; then
    pass "browser windows fill each output ($geoms)"
else
    fail "browser windows: $geoms"
fi
check "room browser is muted" pgrep -f 'kidnyce-kiosk/crowd.*--mute-audio'
DISPLAY=:91 import -window root "$WORK/two-screens.png" 2>/dev/null

kill "$(pgrep -f 'kidnyce-kiosk/crowd' -o)" 2>/dev/null
check "a crashed browser is relaunched" wait_log "started crowd screen" 2

status=$("$CLI" screens)
if grep -qF 'DUMMY0 [DUMMY0]: TV, showing singer' <<<"$status" &&
    grep -qF 'DUMMY1 [DUMMY1]: TV, showing crowd' <<<"$status" && grep -q 'checked in' <<<"$status"; then
    pass "the app sees both ports and what each shows"
else
    fail "the app sees both ports and what each shows: $(tr '\n' '|' <<<"$status")"
fi

echo "== Host picks one screen for everything"
"$CLI" screens single >/dev/null
check "one screen comes up within seconds" wait_log "started all screen at 1920x1080+0+0: $APP/splash?all=1"
sleep 3
check "the second TV is turned off" grep -qx "DUMMY1 off" <<<"$(outputs 91)"
check "only one browser left" test -z "$(pgrep -f 'kidnyce-kiosk/(singer|crowd)')"
DISPLAY=:91 import -window root -crop 1920x1080+0+0 "$WORK/one-screen.png" 2>/dev/null

echo "== Host moves it to the other port"
"$CLI" screens port DUMMY1 >/dev/null
wait_log "started all screen" 2
sleep 3
layout=$(outputs 91)
check "everything on DUMMY1, DUMMY0 off" \
    bash -c "grep -qx 'DUMMY1 1920x1080+0+0' <<<'$layout' && grep -qx 'DUMMY0 off' <<<'$layout'"

echo "== Host goes back to two screens, swapped"
"$CLI" screens swap on >/dev/null
"$CLI" screens dual >/dev/null
wait_log "started crowd screen" 3
sleep 3
layout=$(outputs 91)
check "singer on DUMMY1, room on DUMMY0" \
    bash -c "grep -qx 'DUMMY1 1920x1080+0+0' <<<'$layout' && grep -qx 'DUMMY0 1920x1080+1920+0' <<<'$layout'"
check "the app agrees the singer is on DUMMY1" grep -F 'DUMMY1 [DUMMY1]: TV, showing singer' <<<"$("$CLI" screens)"
before=$(grep -c started "$WORK/kiosk.log")
sleep 8
check "no relaunch while nothing changes" test "$(grep -c started "$WORK/kiosk.log")" -eq "$before"

echo "== Host picks the headphone jack and a volume"
"$CLI" audio headphones >/dev/null
check "a new sound output relaunches the screens" wait_log "started crowd screen" 4
check "the singer browser plays to the headphone jack" pgrep -f 'kidnyce-kiosk/singer.*--alsa-output-device=plughw:CARD=Headphones,DEV=0'
before=$(grep -c started "$WORK/kiosk.log")
"$CLI" audio volume 60 >/dev/null
# No Headphones card here, so the session logs that it tried, without a relaunch.
check "the volume reaches the session" wait_log "Headphones"
check "a volume change does not relaunch" test "$(grep -c started "$WORK/kiosk.log")" -eq "$before"
stop_all
host_defaults

echo "== One TV, Auto"
start_x 92
start_session 92
wait_log "started all screen"
sleep 2
check "one TV shows everything" grep -F "started all screen at 1920x1080+0+0: $APP/splash?all=1" "$WORK/kiosk.log"
check "no room screen without a second TV" test "$(started crowd)" -eq 0
stop_all

echo "== Desktop mode: the desktop owns the monitors"
start_x 93
DISPLAY=:93 xrandr --addmode DUMMY1 1280x1024
DISPLAY=:93 xrandr --output DUMMY0 --mode 1600x1200 --output DUMMY1 --mode 1280x1024 --pos 1600x0
start_session 93 --desktop
wait_log "started crowd screen"
sleep 3
layout=$(outputs 93)
check "the desktop's monitor modes are left alone" \
    bash -c "grep -qx 'DUMMY0 1600x1200+0+0' <<<'$layout' && grep -qx 'DUMMY1 1280x1024+1600+0' <<<'$layout'"
check "singer screen fills the first monitor" grep -F "started singer screen at 1600x1200+0+0" "$WORK/kiosk.log"
check "room screen fills the second monitor" grep -F "started crowd screen at 1280x1024+1600+0" "$WORK/kiosk.log"
session :93 --desktop
check "a second click leaves the open screens alone" grep -F "the screens are already open" "$WORK/kiosk.log"
check "still one singer screen" test "$(grep -c 'started singer screen' "$WORK/kiosk.log")" -eq 1
kill "$(pgrep -f 'kidnyce-kiosk/crowd' -o)" 2>/dev/null
check "closing a screen closes them all" wait_log "a screen was closed"
sleep 2
check "the session ended" bash -c "! kill -0 $S_PID 2>/dev/null"
check "no browsers left" test -z "$(pgrep -f 'kidnyce-kiosk/(singer|crowd)')"

start_session 93 --desktop
wait_log "started crowd screen"
session :93 --stop
sleep 3
check "Close Karaoke Screens closes them" bash -c "! kill -0 $S_PID 2>/dev/null && test -z \"\$(pgrep -f 'kidnyce-kiosk/(singer|crowd)')\""

echo "== Desktop login"
start_session 93 --autostart
check "autostart does nothing while the setting is off" wait_log "opening the screens is off"
"$CLI" desktop-autostart on >/dev/null
start_session 93 --autostart
check "autostart opens the screens when the setting is on" wait_log "started crowd screen"
"$CLI" desktop-autostart off >/dev/null
stop_all

echo
echo "Passed: $PASS  Failed: $FAIL  (screenshots: $WORK/two-screens.png $WORK/one-screen.png)"
((FAIL == 0))
