#!/usr/bin/env bash
# The karaoke screens. Two ways to run:
#
#   Karaoke mode (default): the X session itself, started by startx from the
#   kiosk user's login on tty1, so a crash drops back to getty, which logs in
#   and starts it again. It sets each TV's mode and turns unused ports off.
#
#   --desktop: inside a normal desktop session, from the Karaoke Screens icon.
#   It leaves the desktop's monitor layout alone, plays sound through the
#   desktop's PipeWire, and closing a screen (Alt+F4) closes them all.
#   --autostart does the same at desktop login, only when Settings, Screens
#   says to. --stop closes a desktop run.
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

DESKTOP=0 AUTOSTART=0 STOP=0
for arg in "$@"; do
    case "$arg" in
        --desktop) DESKTOP=1 ;;
        --autostart) DESKTOP=1 AUTOSTART=1 ;;
        --stop) STOP=1 ;;
        *) echo "usage: $0 [--desktop|--autostart|--stop]" >&2; exit 2 ;;
    esac
done

CONFIG=${KIDNYCE_KIOSK_CONFIG:-/etc/kidnyce-karaoke/kiosk.env}
# shellcheck source=/dev/null
[[ -f "$CONFIG" ]] && source "$CONFIG"

APP_URL=${APP_URL:-http://127.0.0.1:5555}
DISPLAY_MODE=${DISPLAY_MODE:-1920x1080}
AUDIO_OUT=${AUDIO_OUT:-headphones}
POLL_SECONDS=${POLL_SECONDS:-5}
PROFILE_ROOT=${PROFILE_ROOT:-$HOME/.config/kidnyce-kiosk}
if ((DESKTOP || STOP)); then
    mkdir -p "$HOME/.cache/kidnyce-karaoke"
    LOG=${KIOSK_LOG:-$HOME/.cache/kidnyce-karaoke/screens.log}
else
    LOG=${KIOSK_LOG:-$HOME/kiosk.log}
fi
RUN_DIR=${XDG_RUNTIME_DIR:-$HOME/.cache/kidnyce-karaoke}
PID_FILE=$RUN_DIR/kidnyce-karaoke-screens.pid

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

browser_bin() {
    command -v chromium || command -v chromium-browser
}

# Under a Wayland desktop, Xwayland names the ports HDMI-A-1 and HDMI-A-2.
# The app and the sound mapping know them as HDMI-1 and HDMI-2, as X does.
connected_outputs() {
    xrandr --query | awk '$2 == "connected" {n = $1; sub(/^HDMI-A-/, "HDMI-", n); print n}'
}

# "1920x1080+1920+0" for an output, or nothing when it is off.
geometry_of() {
    local alt=$1
    [[ $1 =~ ^HDMI-([0-9]+)$ ]] && alt=HDMI-A-${BASH_REMATCH[1]}
    xrandr --query | awk -v o="$1" -v a="$alt" '($1 == o || $1 == a) && $2 == "connected" {
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
    case "$SOUND_OUT" in
        hdmi-singer | hdmi-crowd)
            port=$sound_output
            [[ $SOUND_OUT == hdmi-crowd && -n $crowd_output ]] && port=$crowd_output
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
            log "sound output usb, but no USB audio device found, using default"
            echo "default"
            ;;
        *) echo "$SOUND_OUT" ;;
    esac
}

# The output the host chose in Settings, Sound, if it is one this session can
# use; otherwise AUDIO_OUT from kiosk.env, the installer's choice.
sound_choice() {
    case "$1" in
        hdmi-singer | hdmi-crowd | headphones | usb | default) echo "$1" ;;
        *:*) if [[ $1 =~ ^[A-Za-z0-9:=,_.-]+$ ]]; then echo "$1"; else echo "$AUDIO_OUT"; fi ;;
        *) echo "$AUDIO_OUT" ;;
    esac
}

# On a desktop PipeWire holds the sound cards, so the browser plays through
# the PipeWire sink of the chosen card instead of opening it directly.
# Prints the sink name for an ALSA device such as plughw:CARD=Headphones,DEV=0.
pulse_sink() {
    local id index
    id=$(sed -n 's/.*CARD=\([^,]*\).*/\1/p' <<<"$1")
    [[ -n $id ]] && command -v pactl >/dev/null || return 1
    index=$(readlink "/proc/asound/$id" 2>/dev/null) || return 1
    index=${index#card}
    LC_ALL=C pactl list sinks 2>/dev/null | awk -v c="$index" '
        /^Sink #/ {name = ""}
        /^[ \t]+Name: / {name = $2}
        $1 == "alsa.card" {v = $3; gsub(/"/, "", v); if (v == c && name != "") {print name; exit}}' | grep .
}

# Set the hardware volume of the card behind an ALSA device, such as
# plughw:CARD=Headphones,DEV=0, on its first playback volume control. The
# app keeps the level, so it comes back on every start without alsactl.
apply_level() {
    local device=$1 level=$2 card control
    [[ $level =~ ^[0-9]+$ ]] && ((level <= 100)) || return 0
    if [[ -n $SOUND_SINK ]]; then
        if pactl set-sink-volume "$SOUND_SINK" "$level%" 2>/dev/null; then
            pactl set-sink-mute "$SOUND_SINK" 0 2>/dev/null
            log "volume: $SOUND_SINK at $level%"
        else
            log "could not set $SOUND_SINK to $level%"
        fi
        return 0
    fi
    card=$(sed -n 's/.*CARD=\([^,]*\).*/\1/p' <<<"$device")
    [[ -n $card ]] || return 0
    control=$(amixer -c "$card" scontents 2>/dev/null | awk -F"'" '
        /^Simple mixer control/ {name = $2}
        /Capabilities:.*pvolume/ {print name; exit}')
    if [[ -z $control ]]; then
        log "$card has no playback volume control; level $level% not applied"
        return 0
    fi
    amixer -q -c "$card" sset "$control" "$level%" unmute 2>/dev/null ||
        amixer -q -c "$card" sset "$control" "$level%" 2>/dev/null ||
        { log "could not set $card $control to $level%"; return 0; }
    log "volume: $card $control at $level%"
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
        "${BROWSER_FLAGS[@]}" \
        "$@" \
        "$url" >>"$LOG" 2>&1 9>&- &
    PIDS[$name]=$!
    log "started $name screen at ${w}x${h}+${x}+${y}: $url"
}

# The app's plan for these ports, one "role output path" line per screen.
# Fails when the app does not answer.
fetch_layout() {
    curl --silent --fail --max-time 3 --get \
        --data-urlencode "connected=$(paste -sd, - <<<"$1")" \
        "$APP_URL/api/display/layout" |
        jq -r '(.screens[] | "\(.role) \(.output) \(.path)"), "sound \((.audio.output // "") | if . == "" then "-" else . end) \(.audio.level // -1)"'
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
        # A desktop port the desktop has turned off is not used.
        ((DESKTOP)) && [[ -z $(geometry_of "$output") ]] && continue
        roles+=("$role") ports+=("$output") paths+=("$path")
    done <<<"$plan"
    ((${#roles[@]})) || return 1

    # On a desktop the monitor layout belongs to the desktop.
    while ((!DESKTOP)) && read -r output; do
        [[ -z $output ]] && continue
        if [[ " ${ports[*]} " != *" $output "* ]]; then
            xrandr --output "$output" --off
            log "$output: TV plugged in but not used, turned off"
        fi
    done <<<"$outputs"

    local i
    for i in "${!roles[@]}"; do
        if ((!DESKTOP)); then
            if [[ -z $previous ]]; then
                set_mode "${ports[i]}" --pos 0x0 --primary
            else
                set_mode "${ports[i]}" --right-of "$previous"
            fi
        fi
        previous=${ports[i]}
        [[ ${roles[i]} == crowd ]] && crowd=${ports[i]}
        [[ ${roles[i]} != crowd ]] && sound=${ports[i]}
    done

    local device
    device=$(alsa_device "$sound" "$crowd")
    SOUND_DEVICE=$device SOUND_SINK=""
    local -a audio_flags=()
    unset PULSE_SINK
    if ((DESKTOP)) && SOUND_SINK=$(pulse_sink "$device"); then
        export PULSE_SINK=$SOUND_SINK
        device="pipewire $SOUND_SINK"
    elif ((DESKTOP)); then
        # PipeWire holds the sound cards on a desktop, so opening one directly
        # would play nothing. The desktop's own output is the safe choice.
        [[ $device != default ]] && log "no PipeWire output found for $device (is pactl installed?), using the desktop's sound output"
        device="the desktop's sound output"
    elif [[ $device != pulse ]]; then
        audio_flags=(--alsa-output-device="$device")
    fi

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

# Close a desktop run started from the icon or at login.
stop_desktop() {
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null) || { echo "The karaoke screens are not open."; return 0; }
    # A stale file could name some other process by now: check it is this script.
    if [[ $pid =~ ^[0-9]+$ ]] && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q 'kiosk-session' &&
        kill "$pid" 2>/dev/null; then
        echo "Closing the karaoke screens."
    else
        echo "The karaoke screens are not open."
    fi
    rm -f "$PID_FILE"
}

# At desktop login, open the screens only when the host turned that on.
autostart_wanted() {
    curl --silent --fail --max-time 3 --get \
        --data-urlencode "connected=$(connected_outputs | paste -sd, -)" "$APP_URL/api/display/layout" |
        jq -e '.desktop.autostart == true' >/dev/null
}

main() {
    if ((STOP)); then
        stop_desktop
        exit 0
    fi
    BROWSER=$(browser_bin) || { log "chromium is not installed"; exit 1; }
    declare -gA PIDS=()
    BROWSER_FLAGS=()

    if ((DESKTOP)); then
        mkdir -p "$RUN_DIR"
        # One run per desktop: a second click leaves the open screens alone.
        exec 9>"$RUN_DIR/kidnyce-karaoke-screens.lock"
        if ! flock -n 9; then
            log "the screens are already open"
            exit 0
        fi
        echo $$ >"$PID_FILE"
        # Wayland desktops: Chromium under Xwayland so it can be placed per port.
        [[ -n ${WAYLAND_DISPLAY:-} ]] && BROWSER_FLAGS+=(--ozone-platform=x11)
    else
        # Never blank, never sleep.
        xset s off s noblank -dpms 2>/dev/null
        # Without PipeWire or PulseAudio in this session Chromium uses ALSA directly.
        # Pointing Pulse at nothing makes that choice explicit unless asked for.
        if [[ $AUDIO_OUT != pulse ]]; then
            export PULSE_SERVER=unix:/nonexistent
        fi
    fi

    wait_for_app
    if ((AUTOSTART)) && ! autostart_wanted; then
        log "desktop login: opening the screens is off in Settings, Screens"
        rm -f "$PID_FILE"
        exit 0
    fi
    trap 'stop_all; ((DESKTOP)) && rm -f "$PID_FILE"; exit 0' TERM INT
    ((DESKTOP)) && log "desktop run started"

    local now reply plan current_plan="" current_outputs="" name dead sound_line choice level applied_level=""
    SOUND_OUT=$AUDIO_OUT SOUND_DEVICE="" SOUND_SINK=""
    while true; do
        now=$(connected_outputs)
        if reply=$(fetch_layout "$now"); then
            plan=$(grep -v '^sound ' <<<"$reply" || true)
            sound_line=$(grep '^sound ' <<<"$reply" || true)
            read -r _ choice level <<<"$sound_line"
            # An output change needs the browsers relaunched, so it is part of the plan.
            SOUND_OUT=$(sound_choice "$choice")
            [[ -n $plan ]] && plan+=$'\n'"sound $SOUND_OUT"
        else
            # App restarting: keep what is on screen. Never answered yet: show something.
            if [[ -n $current_plan ]]; then plan=$current_plan; else plan=$(fallback_layout "$now"); fi
            level=$applied_level
        fi
        dead=0
        for name in "${!PIDS[@]}"; do
            kill -0 "${PIDS[$name]}" 2>/dev/null || dead=1
        done
        # On a desktop, closing a screen means done: back to the desktop.
        if ((DESKTOP && dead)); then
            log "a screen was closed: closing the karaoke screens"
            stop_all
            rm -f "$PID_FILE"
            exit 0
        fi
        if [[ $plan != "$current_plan" || $now != "$current_outputs" || $dead == 1 ]]; then
            if ((${#PIDS[@]})); then
                log "screens changed, setting changed or a browser exited: relaunching"
                stop_all
            fi
            current_plan="" current_outputs=$now
            if [[ -n $plan ]] && apply_layout "$plan" "$now"; then
                current_plan=$plan
                applied_level=""
            else
                log "no screen to show on (outputs: $(paste -sd, - <<<"${now:-none}")), waiting"
            fi
        fi
        # The level changes in place: no relaunch, the song keeps playing.
        if [[ -n $SOUND_DEVICE && ${level:-} != "$applied_level" ]]; then
            apply_level "$SOUND_DEVICE" "${level:--1}"
            applied_level=${level:-}
        fi
        sleep "$POLL_SECONDS"
    done
}

main "$@"
