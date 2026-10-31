#!/usr/bin/env bash
# What the TV shows after someone closes the karaoke screens with the keyboard
# (Alt+F4 or Ctrl+W) in karaoke mode. Runs as the kiosk user on tty1 and
# returns when the screens should start again. The app keeps running the whole
# time, so phones still work.
#
#   Enter  start the screens again (also after 10 minutes with no key)
#   W      join a WiFi network (nmtui)
#   D      show the desktop until the next reboot; asks for the admin password,
#          which root checks against the app before starting the desktop
set -uo pipefail

REQUEST_DIR=${KIOSK_REQUEST_DIR:-/run/kidnyce-karaoke}
IDLE_SECONDS=${IDLE_SECONDS:-600}

lan_ip() {
    ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}'
}

has_desktop() {
    local unit
    for unit in lightdm gdm3 gdm sddm greetd; do
        systemctl cat "$unit.service" >/dev/null 2>&1 && return 0
    done
    return 1
}

menu() {
    clear
    printf '\n  Kid Nyce Karaoke: screens closed\n'
    printf '  The app keeps running. Phones still work.\n\n'
    printf '  Enter   start the screens again\n'
    printf '  W       join a WiFi network\n'
    if has_desktop; then
        printf '  D       show the desktop (asks for the admin password)\n'
    fi
    printf '\n  Address: %s\n' "$(lan_ip || true)"
    printf '  The screens start again by themselves after %d minutes.\n\n' $((IDLE_SECONDS / 60))
}

# Hand the password to root through a private file, never on a command line.
request_desktop() {
    local password result="$REQUEST_DIR/desktop-result" _
    read -rsp "  Admin password: " password
    echo
    rm -f "$result"
    if ! (umask 077 && printf '%s\n' "$password" >"$REQUEST_DIR/desktop-request.new") ||
        ! mv -f "$REQUEST_DIR/desktop-request.new" "$REQUEST_DIR/desktop-request"; then
        echo "  Could not ask for the desktop (is the installer up to date?)."
        sleep 3
        return
    fi
    unset password
    printf '  Starting the desktop...\n'
    for _ in $(seq 1 20); do
        if [[ -f $result ]]; then
            case "$(<"$result")" in
                denied) echo "  Wrong admin password." ;;
                none) echo "  No desktop is installed." ;;
                *) echo "  $(<"$result")" ;;
            esac
            rm -f "$result"
            sleep 3
            return
        fi
        sleep 1
    done
    # Root stops this console when the desktop starts, so getting here is unusual.
    echo "  No answer. Try again, or reboot."
    sleep 3
}

while true; do
    menu
    if ! read -rsn1 -t "$IDLE_SECONDS" key; then
        exit 0
    fi
    case "$key" in
        "") exit 0 ;;
        w | W)
            if command -v nmtui >/dev/null; then
                nmtui connect || true
            else
                echo "  NetworkManager is not installed."
                sleep 3
            fi
            ;;
        d | D) has_desktop && request_desktop ;;
    esac
done
