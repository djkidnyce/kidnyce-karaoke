#!/usr/bin/env bash
# X session for the karaoke screens. Started by startx from the kiosk user's
# login on tty1, so a crash drops back to getty, which logs in and starts it
# again.
#
# Every few seconds it tells the app which ports X sees connected and asks
# which page goes on which port. The host picks the setup in Settings, Screens:
#
#   Two screens: the singer screen (lyrics video, the player's sound, who is
#     next, no QR code) and the room screen (QR code with the join code, the
#     singing order, a muted copy of the lyrics video to sing along).
#   One screen for everything: lyrics, QR code, join code and singing order.
#   Auto detect: two screens when two TVs are plugged in and the board has the
#     memory for both, else one.
#
# Plugging a TV in or out, or changing the setting, relaunches the windows.
set -uo pipefail

CONFIG=${KIDNYCE_KIOSK_CONFIG:-/etc/kidnyce-karaoke/kiosk.env}
# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"

APP_URL=${APP_URL:-http://127.0.0.1:5555}
DISPLAY_MODE=${DISPLAY_MODE:-1920x1080}
AUDIO_OUT=${AUDIO_OUT:-hdmi-singer}
POLL_SECONDS=${POLL_SECONDS:-5}
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

# ALSA card behind an HDMI port on a Pi: X's HDMI-1 is the board's HDMI0.
hdmi_card() {
    case "$1" in
        HDMI-1) echo vc4hdmi0 ;;
        HDMI-2) echo vc4hdmi1 ;;
    esac
}

# ALSA device for the player's sound, given the port of the screen that plays
# it and the port of the room screen (empty with one screen). vc4 HDMI needs the
# "hdmi:" PCM, which formats IEC958 frames; plughw on it fails.
alsa_device() {
    local sound_output=$1 crowd_output=$2 port card
    case "$AUDIO_OUT" in
        hdmi-singer | hdmi-crowd)
            port=$sound_output
            [[ $AUDIO_OUT == hdmi-crowd && -n $crowd_output ]] && port=$crowd_output
            card=$(hdmi_card "$port")
            if [[ -n $card ]]; then echo "hdmi:CARD=$card,DEV=0"; else echo "default"; fi
            ;;
        headphones) echo "plughw:CARD=Headphones,DEV=0" ;;
        usb)
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

# The app's plan for these ports, one "role output path" line per screen.
# Fails when the app does not answer.
fetch_layout() {
    curl --silent --fail --max-time 3 --get \
        --data-urlencode "connected=$(paste -sd, - <<<"$1")" \
        "$APP_URL/api/display/layout" | jq -r '.screens[] | "\(.role) \(.output) \(.path)"'
}

# Until the app has answered once: everything on the first port.
fallback_layout() {
    local first
    first=$(head -n 1 <<<"$1")
    [[ -n $first ]] && echo "all $first /splash?all=1"
}

# Turn the ports on, place one Chromium per screen, turn unused ports off.
apply_layout() {
    local plan=$1 outputs=$2 role output path previous="" sound="" crowd=""
    local -a roles=() ports=() paths=()
    while read -r role output path; do
        [[ $role =~ ^(singer|crowd|all)$ && $path =~ ^/[A-Za-z0-9/?=\&_.-]*$ ]] || continue
        grep -qxF -- "$output" <<<"$outputs" || continue
        roles+=("$role") ports+=("$output") paths+=("$path")
    done <<<"$plan"
    ((${#roles[@]})) || return 1

    while read -r output; do
        [[ -z $output ]] && continue
        if [[ " ${ports[*]} " != *" $output "* ]]; then
            xrandr --output "$output" --off
            log "$output: TV plugged in but not used, turned off"
        fi
    done <<<"$outputs"

    local i
    for i in "${!roles[@]}"; do
        if [[ -z $previous ]]; then
            set_mode "${ports[i]}" --pos 0x0 --primary
        else
            set_mode "${ports[i]}" --right-of "$previous"
        fi
        previous=${ports[i]}
        [[ ${roles[i]} == crowd ]] && crowd=${ports[i]}
        [[ ${roles[i]} != crowd ]] && sound=${ports[i]}
    done

    local device
    device=$(alsa_device "$sound" "$crowd")
    local -a audio_flags=()
    [[ $device != pulse ]] && audio_flags=(--alsa-output-device="$device")

    for i in "${!roles[@]}"; do
        if [[ ${roles[i]} == crowd ]]; then
            launch crowd "$(geometry_of "${ports[i]}")" "$APP_URL${paths[i]}" --mute-audio
        else
            launch "${roles[i]}" "$(geometry_of "${ports[i]}")" "$APP_URL${paths[i]}" "${audio_flags[@]}"
        fi
    done
    log "layout: $(tr '\n' ' ' <<<"$plan")(sound: $device)"
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

    # Never blank, never sleep.
    xset s off s noblank -dpms 2>/dev/null

    # Without PipeWire or PulseAudio in this session Chromium uses ALSA directly.
    # Pointing Pulse at nothing makes that choice explicit unless asked for.
    if [[ $AUDIO_OUT != pulse ]]; then
        export PULSE_SERVER=unix:/nonexistent
    fi

    wait_for_app
    trap 'stop_all; exit 0' TERM INT

    local now plan current_plan="" current_outputs="" name dead
    while true; do
        now=$(connected_outputs)
        if ! plan=$(fetch_layout "$now"); then
            # App restarting: keep what is on screen. Never answered yet: show something.
            if [[ -n $current_plan ]]; then plan=$current_plan; else plan=$(fallback_layout "$now"); fi
        fi
        dead=0
        for name in "${!PIDS[@]}"; do
            kill -0 "${PIDS[$name]}" 2>/dev/null || dead=1
        done
        if [[ $plan != "$current_plan" || $now != "$current_outputs" || $dead == 1 ]]; then
            if ((${#PIDS[@]})); then
                log "screens changed, setting changed or a browser exited: relaunching"
                stop_all
            fi
            current_plan="" current_outputs=$now
            if [[ -n $plan ]] && apply_layout "$plan" "$now"; then
                current_plan=$plan
            else
                log "no screen to show on (outputs: $(paste -sd, - <<<"${now:-none}")), waiting"
            fi
        fi
        sleep "$POLL_SECONDS"
    done
}

main "$@"
