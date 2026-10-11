#!/usr/bin/env bash
# Tests deploy/kiosk/kiosk-session.sh against a real Xorg server with two
# virtual outputs (xserver-xorg-video-dummy), standing in for the Pi's two
# HDMI ports. Needs a running app on 127.0.0.1:5555, Xorg with the dummy
# driver, xrandr, xwininfo, xdotool, and a Chromium binary.
#
#   sudo CHROME=/path/to/chrome deploy/tests/kiosk-xdummy-test.sh
#
# Checks: both outputs get the requested mode side by side, the singer and crowd
# browsers land on the right output with the right URL, a crashed browser is
# relaunched, and with one screen the normal splash page is shown.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION="$HERE/../kiosk/kiosk-session.sh"
CHROME=${CHROME:?set CHROME to a Chromium binary}
WORK=$(mktemp -d)
PASS=0
FAIL=0
X_PID=""
S_PID=""

pass() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$*"; }

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
cat >"$WORK/kiosk.env" <<'EOF'
APP_URL=http://127.0.0.1:5555
SINGER_OUTPUT=DUMMY0
CROWD_OUTPUT=DUMMY1
DISPLAY_MODE=1920x1080
AUDIO_OUT=default
SWAP_SCREENS=0
EOF

start_x() {
    Xorg ":$1" -config "$WORK/xorg.conf" -noreset -nolisten tcp -logfile "$WORK/xorg-$1.log" >/dev/null 2>&1 &
    X_PID=$!
    sleep 3
}

start_session() {
    : >"$WORK/kiosk.log"
    env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
        PATH="$WORK/bin:$PATH" HOME="$WORK/home" KIDNYCE_KIOSK_CONFIG="$WORK/kiosk.env" \
        KIOSK_LOG="$WORK/kiosk.log" DISPLAY=":$1" "$SESSION" >/dev/null 2>&1 &
    S_PID=$!
}

stop_all() {
    [[ -n $S_PID ]] && kill "$S_PID" 2>/dev/null
    sleep 1
    pkill -x chrome 2>/dev/null
    [[ -n $X_PID ]] && kill "$X_PID" 2>/dev/null
    sleep 1
    S_PID=""
    X_PID=""
}
trap stop_all EXIT

# Window geometry as WxH+X+Y for every visible top level Chromium window.
windows() {
    local id
    for id in $(DISPLAY=":$1" xdotool search --onlyvisible --name 'PiKaraoke' 2>/dev/null); do
        DISPLAY=":$1" xwininfo -id "$id" | awk '/Absolute upper-left X/ {x = $NF} /Absolute upper-left Y/ {y = $NF}
            /^  Width/ {w = $NF} /^  Height/ {h = $NF} END {print w "x" h "+" x "+" y}'
    done | sort
}

echo "== Two screens"
start_x 91
# Plug in the second "TV": give DUMMY1 a mode, at an odd size and place the
# session has to correct.
DISPLAY=:91 xrandr --addmode DUMMY1 1920x1080
DISPLAY=:91 xrandr --addmode DUMMY1 1280x1024
DISPLAY=:91 xrandr --output DUMMY0 --mode 1600x1200 --output DUMMY1 --mode 1280x1024 --pos 1600x0
start_session 91
sleep 20
layout=$(DISPLAY=:91 xrandr --query | awk '$2 == "connected" {print $1, ($3 == "primary" ? $4 : $3)}')
if grep -qx "DUMMY0 1920x1080+0+0" <<<"$layout" && grep -qx "DUMMY1 1920x1080+1920+0" <<<"$layout"; then
    pass "outputs set to 1920x1080, side by side"
else
    fail "layout: $layout"
fi
if grep -q "started singer screen at 1920x1080+0+0: http://127.0.0.1:5555/splash?singer=1" "$WORK/kiosk.log"; then
    pass "singer screen on DUMMY0 without the QR code"
else
    fail "singer launch: $(grep started "$WORK/kiosk.log")"
fi
if grep -q "started crowd screen at 1920x1080+1920+0: http://127.0.0.1:5555/crowd" "$WORK/kiosk.log"; then
    pass "crowd screen on DUMMY1"
else
    fail "crowd launch: $(grep started "$WORK/kiosk.log")"
fi
geoms=$(windows 91 | tr '\n' ' ')
# Without a window manager a borderless window can report 1px under its request.
if [[ $geoms =~ (1919|1920)x(1079|1080)\+0\+0 && $geoms =~ (1919|1920)x(1079|1080)\+1920\+0 ]]; then
    pass "browser windows fill each output ($geoms)"
else
    fail "browser windows: $geoms"
fi
if pgrep -f 'kidnyce-kiosk/crowd.*--mute-audio' >/dev/null; then
    pass "crowd browser is muted"
else
    fail "crowd browser not started with --mute-audio"
fi
DISPLAY=:91 import -window root "$WORK/two-screens.png" 2>/dev/null

crowd_pid=$(pgrep -f 'kidnyce-kiosk/crowd' -o)
kill "$crowd_pid" 2>/dev/null
sleep 12
if [[ $(grep -c "started crowd screen" "$WORK/kiosk.log") -ge 2 ]]; then
    pass "a crashed browser is relaunched"
else
    fail "no relaunch after the crowd browser died"
fi
stop_all

echo "== One screen"
start_x 92
start_session 92
sleep 15
if grep -q "started singer screen at 1920x1080+0+0: http://127.0.0.1:5555/splash$" "$WORK/kiosk.log" &&
    ! grep -q "started crowd screen" "$WORK/kiosk.log"; then
    pass "one screen shows the normal splash page with its QR code"
else
    fail "single screen: $(grep started "$WORK/kiosk.log")"
fi
stop_all

echo
echo "Passed: $PASS  Failed: $FAIL  (screenshot: $WORK/two-screens.png)"
((FAIL == 0))
