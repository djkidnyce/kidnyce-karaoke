#!/usr/bin/env bash
# X session for the two karaoke screens. Started by startx from the kiosk
# user's login on tty1, so a crash drops back to getty, which logs in and starts
# it again.
#
#   Singer screen (default HDMI-1, the micro HDMI port next to USB-C):
#     the lyrics video, the player's audio, who is next. No QR code.
#   Crowd screen (default HDMI-2): QR code with the join code, the singing
#     order, and a muted copy of the lyrics video so the room can sing along.
#
# With one screen plugged in, it shows the normal splash page, QR code included.
set -uo pipefail

CONFIG=${KIDNYCE_KIOSK_CONFIG:-/etc/kidnyce-karaoke/kiosk.env}
# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"

APP_URL=${APP_URL:-http://127.0.0.1:5555}
SINGER_OUTPUT=${SINGER_OUTPUT:-HDMI-1}
CROWD_OUTPUT=${CROWD_OUTPUT:-HDMI-2}
DISPLAY_MODE=${DISPLAY_MODE:-1920x1080}
AUDIO_OUT=${AUDIO_OUT:-hdmi-singer}
SWAP_SCREENS=${SWAP_SCREENS:-0}
PROFILE_ROOT=${PROFILE_ROOT:-$HOME/.config/kidnyce-kiosk}
LOG=${KIOSK_LOG:-$HOME/kiosk.log}

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

browser_bin() {
    command -v chromium || command -v chromium-browser
}

connected_outputs() {
    xrandr --query | awk '$2 == "connected" {print $1}'
}

# "1920x1080+1920+0" for an output, or nothing when it is off.
geometry_of() {
    xrandr --query | awk -v o="$1" '$1 == o && $2 == "connected" {
        for (i = 3; i <= NF; i++) if ($i ~ /^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$/) {print $i; exit}
    }'
}

set_mode() {
    local output=$1 shift_args=("${@:2}")
    if xrandr --query | awk -v o="$output" -v m="$DISPLAY_MODE" '
        $1 == o {found = 1; next} found && /^[^ ]/ {exit} found && $1 == m {ok = 1} END {exit !ok}'; then
        xrandr --output "$output" --mode "$DISPLAY_MODE" "${shift_args[@]}"
    else
        log "$output has no $DISPLAY_MODE mode, using its preferred mode"
        xrandr --output "$output" --auto "${shift_args[@]}"
    fi
}

# ALSA device for the player's sound. vc4 HDMI needs the "hdmi:" PCM, which
# formats IEC958 frames; plughw on it fails.
alsa_device() {
    local singer_card crowd_card
    if [[ $SINGER_OUTPUT == "HDMI-2" ]]; then singer_card=vc4hdmi1; else singer_card=vc4hdmi0; fi
    if [[ $CROWD_OUTPUT == "HDMI-1" ]]; then crowd_card=vc4hdmi0; else crowd_card=vc4hdmi1; fi
    case "$AUDIO_OUT" in
        hdmi-singer) echo "hdmi:CARD=$singer_card,DEV=0" ;;
        hdmi-crowd) echo "hdmi:CARD=$crowd_card,DEV=0" ;;
        headphones) echo "plughw:CARD=Headphones,DEV=0" ;;
        usb)
            local card
            for card in /proc/asound/card*; do
                if [[ -f "$card/usbid" ]]; then
                    echo "plughw:CARD=$(<"$card/id"),DEV=0"
                    return
                fi
            done
            log "AUDIO_OUT=usb but no USB audio device found, using default"
            echo "default"
            ;;
        *) echo "$AUDIO_OUT" ;;
    esac
}

wait_for_app() {
    for _ in $(seq 1 120); do
        if curl --silent --fail --max-time 2 "$APP_URL/api/healthz" >/dev/null; then
            return 0
        fi
        sleep 1
    done
    log "app did not answer at $APP_URL after 120s, starting screens anyway"
}

# One Chromium per screen, each with its own profile so they run as separate
# windows placed by explicit geometry (there is no window manager to place them).
launch() {
    local name=$1 geometry=$2 url=$3
    shift 3
    local size=${geometry%%+*}
    local offset=${geometry#*+}
    local w=${size%x*} h=${size#*x} x=${offset%+*} y=${offset#*+}
    mkdir -p "$PROFILE_ROOT/$name"
    # A crash leaves "exited uncleanly" behind, which would show a restore bar.
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
        "$PROFILE_ROOT/$name/Default/Preferences" 2>/dev/null
    "$BROWSER" \
        --user-data-dir="$PROFILE_ROOT/$name" \
        --kiosk \
        --window-position="$x,$y" \
        --window-size="$w,$h" \
        --autoplay-policy=no-user-gesture-required \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=Translate,TranslateUI,MediaRouter,DialMediaRouteProvider,HardwareMediaKeyHandling \
        --no-first-run \
        --no-default-browser-check \
        --password-store=basic \
        --check-for-update-interval=31536000 \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        --disable-dev-shm-usage \
        "$@" \
        "$url" >>"$LOG" 2>&1 &
    PIDS[$name]=$!
    log "started $name screen at ${w}x${h}+${x}+${y}: $url"
}

layout_and_launch() {
    local outputs singer crowd
    outputs=$(connected_outputs)
    singer=$SINGER_OUTPUT
    crowd=$CROWD_OUTPUT
    if [[ $SWAP_SCREENS == 1 ]]; then
        singer=$CROWD_OUTPUT
        crowd=$SINGER_OUTPUT
    fi
    local have_singer=0 have_crowd=0
    grep -qx "$singer" <<<"$outputs" && have_singer=1
    grep -qx "$crowd" <<<"$outputs" && have_crowd=1

    if ((have_singer == 0 && have_crowd == 1)); then
        # Only one screen, on the other port: use it as the single screen.
        singer=$crowd
        have_singer=1
        have_crowd=0
    fi
    if ((have_singer == 0)); then
        log "no screen connected (outputs: ${outputs:-none}), waiting"
        return 1
    fi

    set_mode "$singer" --pos 0x0 --primary
    if ((have_crowd)); then
        set_mode "$crowd" --right-of "$singer"
    fi

    local device
    device=$(alsa_device)
    local -a audio_flags=()
    if [[ $device != pulse ]]; then
        audio_flags=(--alsa-output-device="$device")
    fi

    if ((have_crowd)); then
        launch singer "$(geometry_of "$singer")" "$APP_URL/splash?singer=1" "${audio_flags[@]}"
        launch crowd "$(geometry_of "$crowd")" "$APP_URL/crowd" --mute-audio
    else
        launch singer "$(geometry_of "$singer")" "$APP_URL/splash" "${audio_flags[@]}"
    fi
    LAYOUT="$outputs"
}

stop_all() {
    local pid
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    wait 2>/dev/null
    PIDS=()
}

main() {
    BROWSER=$(browser_bin) || { log "chromium is not installed"; exit 1; }
    declare -gA PIDS=()
    LAYOUT=""

    # Never blank, never sleep.
    xset s off s noblank -dpms 2>/dev/null

    # Without PipeWire or PulseAudio in this session Chromium uses ALSA directly.
    # Pointing Pulse at nothing makes that choice explicit unless asked for.
    if [[ $AUDIO_OUT != pulse ]]; then
        export PULSE_SERVER=unix:/nonexistent
    fi

    wait_for_app
    trap 'stop_all; exit 0' TERM INT

    while true; do
        if [[ -z $LAYOUT ]]; then
            layout_and_launch || { sleep 5; continue; }
        fi
        sleep 5
        # Restart on hotplug, or when either browser has died.
        local now name dead=0
        now=$(connected_outputs)
        for name in "${!PIDS[@]}"; do
            kill -0 "${PIDS[$name]}" 2>/dev/null || dead=1
        done
        if [[ $now != "$LAYOUT" || $dead == 1 ]]; then
            log "screens changed or a browser exited, relaunching"
            stop_all
            LAYOUT=""
        fi
    done
}

main "$@"
