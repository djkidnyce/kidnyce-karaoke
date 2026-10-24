#!/usr/bin/env bash
# Kid Nyce Karaoke installer for a Raspberry Pi running Raspberry Pi OS 64 bit,
# Lite or Desktop, Bookworm (Debian 12) or Trixie (Debian 13). Built for a Pi 4B
# with 4 GB or 8 GB and two TVs; a Pi 5 or Pi 400 works the same, and a Pi 3 or
# a 2 GB board runs one screen for everything.
#
# What it sets up, in order:
#   1. System packages (ffmpeg, Nginx, Certbot, nftables, X and Chromium for the screens)
#   2. A locked down service user and folders
#   3. Deno (pinned, checksum verified) so yt-dlp can download from YouTube
#   4. The app in a virtualenv from a hash pinned lock file
#   5. A Let's Encrypt certificate for karaoke.djkidnyce.com by DNS challenge
#      (no ports opened to the internet)
#   6. Nginx in front with TLS, security headers and rate limits
#   7. A Cloudflare DNS record kept pointed at this Pi's LAN address
#   8. An nftables firewall: HTTPS and SSH from private networks only
#   9. The kiosk screens: two (singer and room) when two TVs are plugged in,
#      else one for everything. The host can choose in Settings, Screens.
#
# Run it again any time: it is idempotent and keeps your songs, settings,
# certificate and admin password.
#
# Quick start (on the Pi, from the unpacked release folder):
#   sudo ./deploy/install-pi.sh --cf-token-file ~/cloudflare.token
#
# See deploy/README.md for every option and the Cloudflare token setup.
set -Eeuo pipefail

INSTALLER_VERSION="1.1.0"
APP_VERSION="1.23.0+kidnyce.2"

# Deno is what yt-dlp runs YouTube's player code with. Pinned, and checked
# against the SHA256 published with the release.
DENO_VERSION="2.9.6"
declare -A DENO_SHA256=(
    [aarch64]="9a46afc6c392c7cd2ff71a31558935545b46408d0e87f7a86908c712721c046e"
    [x86_64]="394f07f4da2bebe6ce6f1e7ce0fa16429b29b08c35e3fac3fe25972676dff4b2"
)

# ---------------------------------------------------------------- defaults
DOMAIN="karaoke.djkidnyce.com"
ZONE=""
EMAIL=""
DNS_PROVIDER="cloudflare"
CF_TOKEN_FILE=""
CF_ZONE_ID=""
DNS_AUTH_HOOK=""
DNS_CLEANUP_HOOK=""
STAGING=0
ACME_SERVER=""
ACME_CA_BUNDLE=""
ADMIN_PASSWORD_FILE=""
RESET_ADMIN_PASSWORD=0
KIOSK=1
KIOSK_USER="kiosk"
SCREENS=""
SWAP_SCREENS=0
DISPLAY_MODE="1920x1080"
AUDIO_OUT="hdmi-singer"
FORCE_HDMI=0
FIREWALL=1
DDNS=1
PORT=5555
ASSUME_YES=0
DRY_RUN=0
SKIP_HW_CHECK=0
NO_START=0

PREFIX="/opt/kidnyce-karaoke"
DATA_DIR="/var/lib/kidnyce-karaoke"
ETC_DIR="/etc/kidnyce-karaoke"
APP_USER="pikaraoke"
CERT_NAME="kidnyce-karaoke"
LOG_FILE="/var/log/kidnyce-karaoke-install.log"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

# ---------------------------------------------------------------- output
if [[ -t 1 ]]; then
    C_BOLD=$'\033[1m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_RED=$'\033[31m' C_OFF=$'\033[0m'
else
    C_BOLD="" C_GREEN="" C_YELLOW="" C_RED="" C_OFF=""
fi
STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s[%d] %s%s\n' "$C_BOLD" "$STEP" "$*" "$C_OFF"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '    %sOK%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '    %sWARNING%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; WARNINGS+=("$*"); }
die() { printf '\n%sERROR%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }
WARNINGS=()

trap 'die "failed at line $LINENO: $BASH_COMMAND (full log: $LOG_FILE)"' ERR

usage() {
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
    cat <<EOF

Options:
  --domain NAME            Hostname guests use (default: $DOMAIN)
  --zone NAME              DNS zone that holds it (default: the domain minus its first label)
  --email ADDRESS          Let's Encrypt account email (optional)
  --dns-provider NAME      cloudflare (default), route53, hook, manual or selfsigned
  --cf-token-file PATH     File holding a Cloudflare API token with Zone.DNS Edit on the zone.
                           Also read from \$CF_API_TOKEN, or asked for.
  --cf-zone-id ID          Cloudflare zone id, if the token cannot list zones
  --dns-auth-hook PATH     With --dns-provider hook: Certbot manual auth hook
  --dns-cleanup-hook PATH  With --dns-provider hook: Certbot manual cleanup hook
  --staging                Use the Let's Encrypt staging CA (untrusted certs, for trial runs)
  --admin-password-file P  Use this admin password instead of generating one
  --reset-admin-password   Generate a new admin password even if one exists
  --no-kiosk               Do not set up the HDMI screens
  --kiosk-user NAME        Login that runs the screens (default: $KIOSK_USER)
  --screens MODE           auto (default: two screens when two TVs are plugged in),
                           single (one screen for everything) or dual. Also in Settings.
  --swap-screens           Singer screen on HDMI1 and room screen on HDMI0
  --display-mode WxH       Resolution for the screens (default: $DISPLAY_MODE)
  --audio OUT              hdmi-singer (default), hdmi-crowd, headphones, usb, pulse or an ALSA PCM
  --force-hdmi             Keep both HDMI outputs on even if a TV is off at boot (edits cmdline.txt)
  --no-firewall            Do not install the nftables firewall
  --no-ddns                Do not keep the DNS record pointed at this Pi
  --port N                 Loopback port for the app (default: $PORT)
  --yes                    Do not ask questions
  --dry-run                Show what would happen and change nothing
  --skip-hw-check          Allow hardware other than a Raspberry Pi (testing only)
  --no-start               Configure everything but start nothing (image builds, testing)
  --acme-server URL        Alternate ACME directory (testing only)
  --acme-ca-bundle FILE    CA bundle for that ACME server (testing only)
  -h, --help               This help
EOF
}

# ---------------------------------------------------------------- args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN=$2; shift ;;
        --zone) ZONE=$2; shift ;;
        --email) EMAIL=$2; shift ;;
        --dns-provider) DNS_PROVIDER=$2; shift ;;
        --cf-token-file) CF_TOKEN_FILE=$2; shift ;;
        --cf-zone-id) CF_ZONE_ID=$2; shift ;;
        --dns-auth-hook) DNS_AUTH_HOOK=$2; shift ;;
        --dns-cleanup-hook) DNS_CLEANUP_HOOK=$2; shift ;;
        --staging) STAGING=1 ;;
        --admin-password-file) ADMIN_PASSWORD_FILE=$2; shift ;;
        --reset-admin-password) RESET_ADMIN_PASSWORD=1 ;;
        --no-kiosk) KIOSK=0 ;;
        --kiosk-user) KIOSK_USER=$2; shift ;;
        --screens) SCREENS=$2; shift ;;
        --swap-screens) SWAP_SCREENS=1 ;;
        --display-mode) DISPLAY_MODE=$2; shift ;;
        --audio) AUDIO_OUT=$2; shift ;;
        --force-hdmi) FORCE_HDMI=1 ;;
        --no-firewall) FIREWALL=0 ;;
        --no-ddns) DDNS=0 ;;
        --port) PORT=$2; shift ;;
        --yes | -y) ASSUME_YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --skip-hw-check) SKIP_HW_CHECK=1 ;;
        --no-start) NO_START=1 ;;
        --acme-server) ACME_SERVER=$2; shift ;;
        --acme-ca-bundle) ACME_CA_BUNDLE=$2; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

[[ -n $ZONE ]] || ZONE=${DOMAIN#*.}
[[ $DNS_PROVIDER == cloudflare ]] || DDNS=0

# ---------------------------------------------------------------- helpers
run() {
    if ((DRY_RUN)); then
        printf '    [dry run] %s\n' "$*"
    else
        "$@"
    fi
}

# write_file MODE OWNER PATH < content. Atomic, so a reader never sees half a file.
write_file() {
    local mode=$1 owner=$2 path=$3 tmp
    if ((DRY_RUN)); then
        printf '    [dry run] write %s (%s %s)\n' "$path" "$mode" "$owner"
        cat >/dev/null
        return
    fi
    install -d -m 755 "$(dirname "$path")"
    tmp=$(mktemp "$(dirname "$path")/.kidnyce.XXXXXX")
    cat >"$tmp"
    chmod "$mode" "$tmp"
    chown "$owner" "$tmp"
    mv -f "$tmp" "$path"
}

# render TEMPLATE: replace @@NAME@@ tokens with the values of shell variables.
render() {
    local text
    text=$(<"$1")
    local name
    for name in DOMAIN PORT PREFIX DATA_DIR ETC_DIR APP_USER KIOSK_USER CERT KEY \
        LISTEN_HTTP2 HTTP2_DIRECTIVE; do
        text=${text//@@$name@@/${!name:-}}
    done
    if [[ $text == *@@*@@* ]]; then
        die "unrendered token in $1"
    fi
    printf '%s\n' "$text"
}

has_systemd() { [[ -d /run/systemd/system ]]; }

systemctl_do() {
    if has_systemd; then
        run systemctl "$@"
    else
        info "no systemd running here, skipped: systemctl $*"
    fi
}

confirm() {
    ((ASSUME_YES)) && return 0
    local reply
    read -r -p "    $1 [y/N] " reply </dev/tty || return 1
    [[ $reply =~ ^[Yy] ]]
}

apt_install() {
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}

pkg_available() { apt-cache show "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- 0. preflight
preflight() {
    step "Checking this machine"
    [[ $EUID -eq 0 ]] || die "run with sudo: sudo $0"
    ((BASH_VERSINFO[0] >= 5)) || die "bash 5 or newer is required"

    [[ $DOMAIN =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$ ]] || die "not a valid hostname: $DOMAIN"
    case "$DNS_PROVIDER" in cloudflare | route53 | hook | manual | selfsigned) ;; *) die "unknown --dns-provider $DNS_PROVIDER" ;; esac
    if [[ ! $PORT =~ ^[0-9]+$ ]] || ((PORT <= 1024 || PORT >= 65536)); then
        die "--port must be 1025 to 65535"
    fi
    [[ $DISPLAY_MODE =~ ^[0-9]+x[0-9]+$ ]] || die "--display-mode must look like 1920x1080"
    [[ $KIOSK_USER =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] || die "invalid --kiosk-user"
    [[ -z $SCREENS || $SCREENS =~ ^(auto|single|dual)$ ]] || die "--screens must be auto, single or dual"

    # shellcheck source=/dev/null
    source /etc/os-release
    OS_CODENAME=${VERSION_CODENAME:-unknown}
    info "OS: ${PRETTY_NAME:-unknown}"
    case "$OS_CODENAME" in
        bookworm | trixie) ok "Debian $OS_CODENAME base is supported" ;;
        *)
            if ((SKIP_HW_CHECK)); then warn "untested OS $OS_CODENAME, continuing (--skip-hw-check)"
            else die "Raspberry Pi OS Bookworm or Trixie is required (found $OS_CODENAME)"; fi
            ;;
    esac

    ARCH=$(uname -m)
    if [[ $ARCH != aarch64 ]]; then
        if ((SKIP_HW_CHECK)) && [[ $ARCH == x86_64 ]]; then warn "x86_64 host, continuing (--skip-hw-check)"
        else die "64 bit Raspberry Pi OS is required (found $ARCH). Reflash with the 64 bit image."; fi
    fi

    local model mem_kb mem_gb
    model=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo "not a Raspberry Pi")
    mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
    mem_gb=$(awk -v k="$mem_kb" 'BEGIN {printf "%.1f", k / 1048576}')
    info "Hardware: $model, ${mem_gb} GB RAM"
    case "$model" in
        # "Raspberry Pi 4" also matches the Pi 400, and "Raspberry Pi 5" the Pi 500.
        *"Raspberry Pi 4"* | *"Raspberry Pi 5"* | *"Compute Module 4"* | *"Compute Module 5"*)
            ok "two HDMI ports: one or two screens" ;;
        *"Raspberry Pi 3"* | *"Raspberry Pi Zero 2"*)
            warn "one HDMI port, so one screen shows everything (lyrics, QR code, singing order)"
            [[ $SCREENS == dual ]] && warn "--screens dual needs two HDMI ports; this board uses one screen"
            ;;
        *"Raspberry Pi"*) warn "untested Raspberry Pi model, continuing" ;;
        *)
            if ((SKIP_HW_CHECK)); then warn "not a Raspberry Pi, continuing (--skip-hw-check)"
            else die "this installer targets a Raspberry Pi (found: $model). Use --skip-hw-check to override."; fi
            ;;
    esac
    if ((mem_kb < 900000)); then
        if ((SKIP_HW_CHECK)); then warn "under 1 GB RAM"
        else die "at least 1 GB of RAM is needed for the screen browser and video (found ${mem_gb} GB)"; fi
    elif ((mem_kb < 3500000)); then
        warn "under 4 GB RAM: Auto runs one screen for everything. Two screens can still be forced in Settings, Screens."
    fi

    local free_gb
    free_gb=$(df -Pk / | awk 'NR == 2 {printf "%d", $4 / 1048576}')
    ((free_gb >= 3)) || die "at least 3 GB free disk space is needed (found ${free_gb} GB)"
    ok "${free_gb} GB free on /"

    if ! getent hosts pypi.org >/dev/null; then die "no DNS: this Pi cannot resolve pypi.org. Check the network."; fi
    ok "network and DNS work"

    if has_systemd && [[ $(timedatectl show -p NTPSynchronized --value 2>/dev/null) != yes ]]; then
        warn "the clock is not NTP synchronized yet; TLS can fail if the time is wrong"
    fi

    if [[ -n $ACME_CA_BUNDLE && ! -f $ACME_CA_BUNDLE ]]; then die "--acme-ca-bundle not found: $ACME_CA_BUNDLE"; fi
    if [[ $DNS_PROVIDER == hook ]]; then
        [[ -x $DNS_AUTH_HOOK ]] || die "--dns-provider hook needs an executable --dns-auth-hook"
    fi

    WHEEL=$(find "$SRC_ROOT/dist" -maxdepth 1 -name 'pikaraoke-*.whl' 2>/dev/null | sort | tail -n 1)
    [[ -n $WHEEL ]] || die "no app wheel in $SRC_ROOT/dist. Use the release bundle, or build one with: uv build --wheel"
    [[ -f $SCRIPT_DIR/requirements.lock ]] || die "missing $SCRIPT_DIR/requirements.lock"
    if [[ -f $SRC_ROOT/dist/SHA256SUMS ]]; then
        (cd "$SRC_ROOT/dist" && sha256sum --quiet --check --ignore-missing SHA256SUMS) || die "app wheel checksum mismatch"
        ok "app wheel checksum verified"
    fi
    info "Installing $(basename "$WHEEL") for https://$DOMAIN"
}

# ---------------------------------------------------------------- secrets
CF_TOKEN=""
read_cloudflare_token() {
    [[ $DNS_PROVIDER == cloudflare ]] || return 0
    if [[ -n $CF_TOKEN_FILE ]]; then
        [[ -r $CF_TOKEN_FILE ]] || die "cannot read --cf-token-file $CF_TOKEN_FILE"
        CF_TOKEN=$(tr -d ' \r\n' <"$CF_TOKEN_FILE")
    elif [[ -n ${CF_API_TOKEN:-} ]]; then
        CF_TOKEN=$CF_API_TOKEN
    elif [[ -s $ETC_DIR/cloudflare.token ]]; then
        CF_TOKEN=$(<"$ETC_DIR/cloudflare.token")
        info "reusing the saved Cloudflare token"
    elif ((ASSUME_YES)); then
        die "Cloudflare token needed: --cf-token-file PATH or CF_API_TOKEN (or --dns-provider selfsigned)"
    else
        read -r -s -p "    Cloudflare API token (Zone.DNS Edit on $ZONE): " CF_TOKEN </dev/tty
        echo
    fi
    [[ $CF_TOKEN =~ ^[A-Za-z0-9_-]{20,}$ ]] || die "that does not look like a Cloudflare API token"
}

# ---------------------------------------------------------------- 1. packages
install_packages() {
    step "Installing system packages"
    run apt-get update -q
    local pkgs=(ca-certificates curl jq openssl unzip python3 python3-venv ffmpeg nginx certbot
        nftables sqlite3 fonts-dejavu-core)
    case "$DNS_PROVIDER" in
        cloudflare) pkgs+=(python3-certbot-dns-cloudflare) ;;
        route53) pkgs+=(python3-certbot-dns-route53) ;;
    esac
    if ((KIOSK)); then
        pkgs+=(xserver-xorg xserver-xorg-legacy xinit x11-xserver-utils alsa-utils fonts-noto-color-emoji)
        if pkg_available chromium; then pkgs+=(chromium); else pkgs+=(chromium-browser); fi
    fi
    apt_install "${pkgs[@]}"
    ok "packages installed"
}

# ---------------------------------------------------------------- 2. users and folders
create_users_and_dirs() {
    step "Creating the service user and folders"
    if ! id "$APP_USER" >/dev/null 2>&1; then
        run useradd --system --user-group --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
    fi
    run install -d -m 755 -o root -g root "$PREFIX" "$PREFIX/bin" "$PREFIX/kiosk" "$ETC_DIR"
    run install -d -m 750 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR"
    # Group writable, so the login user can drop songs and break music in over SFTP.
    run install -d -m 2775 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR/songs" "$DATA_DIR/music"
    if [[ -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
        run usermod -a -G "$APP_USER" "$SUDO_USER"
        info "$SUDO_USER can add files to $DATA_DIR/songs and $DATA_DIR/music"
    fi
    ok "user $APP_USER, data in $DATA_DIR"
}

# ---------------------------------------------------------------- 3. deno
install_deno() {
    step "Installing Deno $DENO_VERSION for yt-dlp"
    if [[ -x $PREFIX/bin/deno ]] && "$PREFIX/bin/deno" --version 2>/dev/null | grep -q "deno $DENO_VERSION "; then
        ok "already installed"
        return
    fi
    local triple sha tmp
    triple="$ARCH-unknown-linux-gnu"
    sha=${DENO_SHA256[$ARCH]:-}
    [[ -n $sha ]] || die "no pinned Deno checksum for $ARCH"
    if ((DRY_RUN)); then info "[dry run] download deno $DENO_VERSION $triple and verify $sha"; return; fi
    tmp=$(mktemp -d)
    curl -fsSL --retry 3 -o "$tmp/deno.zip" \
        "https://github.com/denoland/deno/releases/download/v$DENO_VERSION/deno-$triple.zip"
    echo "$sha  $tmp/deno.zip" | sha256sum --quiet --check || { rm -rf "$tmp"; die "Deno checksum mismatch"; }
    unzip -q -o "$tmp/deno.zip" -d "$tmp"
    install -m 755 -o root -g root "$tmp/deno" "$PREFIX/bin/deno"
    rm -rf "$tmp"
    ok "deno $DENO_VERSION verified and installed"
}

# ---------------------------------------------------------------- 4. app
install_app() {
    step "Installing the app (hash pinned)"
    local venv=$PREFIX/venv
    if [[ -x $venv/bin/python ]] && ! "$venv/bin/python" -I -c 'import sys' 2>/dev/null; then
        run rm -rf "$venv"
    fi
    # A Python upgrade (Bookworm to Trixie) leaves a venv pointing at the old one.
    if [[ -x $venv/bin/python ]] && [[ $("$venv/bin/python" -I -c 'import sys; print(sys.version_info[:2])') != $(python3 -c 'import sys; print(sys.version_info[:2])') ]]; then
        run rm -rf "$venv"
    fi
    [[ -x $venv/bin/python ]] || run python3 -m venv "$venv"
    run "$venv/bin/python" -I -m pip install --quiet --disable-pip-version-check \
        --require-hashes --only-binary=:all: -r "$SCRIPT_DIR/requirements.lock"
    run "$venv/bin/python" -I -m pip install --quiet --disable-pip-version-check \
        --no-deps --no-index --force-reinstall "$WHEEL"
    # YouTube changes often; yt-dlp is the one package kept current outside the lock.
    run "$venv/bin/python" -I -m pip install --quiet --disable-pip-version-check \
        --upgrade --only-binary=:all: "yt-dlp[default]"

    # Translations compile into the package once, here: at runtime the install
    # folder is read only to the service.
    local site
    site=$("$venv/bin/python" -I -c 'import os, pikaraoke; print(os.path.dirname(pikaraoke.__file__))' 2>/dev/null || echo "")
    if [[ -n $site ]]; then
        run "$venv/bin/python" -I -m babel.messages.frontend compile -f -d "$site/translations" >/dev/null 2>&1 ||
            warn "translations did not compile; the UI stays in English"
    fi

    run install -m 755 -o root -g root "$SCRIPT_DIR/files/kidnyce-karaoke" /usr/local/bin/kidnyce-karaoke
    run install -m 755 -o root -g root "$SCRIPT_DIR/files/kidnyce-set-admin-password" "$PREFIX/bin/kidnyce-set-admin-password"
    run install -m 755 -o root -g root "$SCRIPT_DIR/files/kidnyce-karaoke-ddns" /usr/local/sbin/kidnyce-karaoke-ddns
    if ((!DRY_RUN)); then
        ok "pikaraoke $("$venv/bin/python" -I -c 'from pikaraoke.version import __version__; print(__version__)'), yt-dlp $("$venv/bin/python" -I -m yt_dlp --version)"
    fi
}

# ---------------------------------------------------------------- 5. app config
FIRST_PASSWORD=""
configure_app() {
    step "Configuring the app service"
    CERT=${CERT:-/etc/letsencrypt/live/$CERT_NAME/fullchain.pem}
    KEY=${KEY:-/etc/letsencrypt/live/$CERT_NAME/privkey.pem}

    write_file 644 root:root "$ETC_DIR/install.env" <<EOF
# Written by deploy/install-pi.sh $INSTALLER_VERSION. Read by kidnyce-karaoke.
DOMAIN=$DOMAIN
ZONE=$ZONE
PORT=$PORT
PREFIX=$PREFIX
DATA_DIR=$DATA_DIR
APP_USER=$APP_USER
CERT=$CERT
KEY=$KEY
KIOSK=$KIOSK
KIOSK_USER=$KIOSK_USER
DNS_PROVIDER=$DNS_PROVIDER
VERSION=$APP_VERSION
EOF
    if [[ ! -f $ETC_DIR/app.env ]]; then
        write_file 640 root:root "$ETC_DIR/app.env" <<'EOF'
# Extra PiKaraoke flags, for example: PIKARAOKE_EXTRA_ARGS="--high-quality --enable-fair-queue"
# Then: sudo systemctl restart kidnyce-karaoke
PIKARAOKE_EXTRA_ARGS=""
EOF
    fi

    render "$SCRIPT_DIR/files/kidnyce-karaoke.service" | write_file 644 root:root /etc/systemd/system/kidnyce-karaoke.service

    # Break music: start with the track bundled with PiKaraoke so breaks work
    # out of the box. Add your own files to the music folder any time.
    if ((!DRY_RUN)) && [[ -z $(find "$DATA_DIR/music" -maxdepth 2 -type f -print -quit) ]]; then
        local bundled
        bundled=$("$PREFIX/venv/bin/python" -I -c 'import os, pikaraoke; print(os.path.join(os.path.dirname(pikaraoke.__file__), "static", "music"))')
        cp -n "$bundled"/*.mp3 "$DATA_DIR/music/" 2>/dev/null || true
        chown -R "$APP_USER:$APP_USER" "$DATA_DIR/music"
    fi

    # Admin password: generated once, kept across reinstalls.
    local pw_file=$ETC_DIR/admin-password
    if [[ -n $ADMIN_PASSWORD_FILE ]]; then
        [[ -r $ADMIN_PASSWORD_FILE ]] || die "cannot read --admin-password-file"
        FIRST_PASSWORD=$(head -n 1 "$ADMIN_PASSWORD_FILE")
    elif [[ ! -s $pw_file || $RESET_ADMIN_PASSWORD == 1 ]]; then
        FIRST_PASSWORD=$(openssl rand -base64 48 | tr -dc 'A-HJ-NP-Za-km-z2-9' | cut -c1-20)
    fi
    if [[ -n $FIRST_PASSWORD ]]; then
        ((${#FIRST_PASSWORD} >= 12)) || die "the admin password must be at least 12 characters"
        printf '%s\n' "$FIRST_PASSWORD" | write_file 600 root:root "$pw_file"
        if ((!DRY_RUN)); then
            "$PREFIX/bin/kidnyce-set-admin-password" <"$pw_file" ||
                die "could not store the admin password"
            # The helper ran as root; make sure the app owns what it wrote.
            chown -R "$APP_USER:$APP_USER" "$DATA_DIR/.pikaraoke"
        fi
        ok "admin password set (shown at the end, and any time with: sudo kidnyce-karaoke password)"
    else
        ok "keeping the existing admin password"
    fi
    systemctl_do daemon-reload
    systemctl_do enable kidnyce-karaoke.service
}

# ---------------------------------------------------------------- 6. certificate
CERT_KIND=""
self_signed_cert() {
    local dir=$ETC_DIR/tls
    CERT=$dir/fullchain.pem
    KEY=$dir/privkey.pem
    CERT_KIND="self signed (browsers will warn)"
    if [[ -f $CERT ]] && openssl x509 -checkend 2592000 -noout -in "$CERT" >/dev/null 2>&1 &&
        openssl x509 -noout -ext subjectAltName -in "$CERT" 2>/dev/null | grep -q "DNS:$DOMAIN"; then
        return
    fi
    run install -d -m 700 -o root -g root "$dir"
    run openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -sha256 -nodes -days 397 \
        -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN" \
        -keyout "$KEY" -out "$CERT"
    run chmod 600 "$KEY"
}

obtain_certificate() {
    step "Getting a TLS certificate for $DOMAIN"
    if [[ $DNS_PROVIDER == selfsigned ]]; then
        self_signed_cert
        warn "self signed certificate: phones will show a warning. Use a DNS provider for a trusted one."
        return
    fi

    local -a args=(certonly --cert-name "$CERT_NAME" -d "$DOMAIN" --key-type ecdsa --elliptic-curve secp256r1
        --keep-until-expiring --agree-tos --deploy-hook "systemctl reload nginx")
    if [[ -n $EMAIL ]]; then args+=(--email "$EMAIL" --no-eff-email); else args+=(--register-unsafely-without-email); fi
    ((STAGING)) && args+=(--staging)
    [[ -n $ACME_SERVER ]] && args+=(--server "$ACME_SERVER")

    case "$DNS_PROVIDER" in
        cloudflare)
            printf 'dns_cloudflare_api_token = %s\n' "$CF_TOKEN" |
                write_file 600 root:root /etc/letsencrypt/kidnyce-cloudflare.ini
            args+=(--non-interactive --dns-cloudflare --dns-cloudflare-credentials /etc/letsencrypt/kidnyce-cloudflare.ini
                --dns-cloudflare-propagation-seconds 30)
            ;;
        route53) args+=(--non-interactive --dns-route53) ;;
        hook)
            args+=(--non-interactive --manual --preferred-challenges dns --manual-auth-hook "$DNS_AUTH_HOOK")
            [[ -n $DNS_CLEANUP_HOOK ]] && args+=(--manual-cleanup-hook "$DNS_CLEANUP_HOOK")
            ;;
        manual)
            warn "manual DNS: you will add a TXT record by hand now, and again before every renewal (about every 60 days)"
            args+=(--manual --preferred-challenges dns)
            ;;
    esac

    local -a env_prefix=()
    [[ -n $ACME_CA_BUNDLE ]] && env_prefix=(env "REQUESTS_CA_BUNDLE=$ACME_CA_BUNDLE")
    if ((DRY_RUN)); then
        info "[dry run] certbot ${args[*]}"
        CERT_KIND="Let's Encrypt"
        return
    fi
    install -d -m 755 /etc/letsencrypt
    if "${env_prefix[@]}" certbot "${args[@]}"; then
        CERT=/etc/letsencrypt/live/$CERT_NAME/fullchain.pem
        KEY=/etc/letsencrypt/live/$CERT_NAME/privkey.pem
        if ((STAGING)) || [[ -n $ACME_SERVER ]]; then CERT_KIND="test CA (not trusted by phones)"; else CERT_KIND="Let's Encrypt"; fi
        ok "certificate ready: $CERT"
        # Certbot renews from its own timer; keep the CA bundle for test servers.
        if [[ -n $ACME_CA_BUNDLE ]] && has_systemd; then
            write_file 644 root:root /etc/systemd/system/certbot.service.d/kidnyce-ca.conf <<EOF
[Service]
Environment=REQUESTS_CA_BUNDLE=$ACME_CA_BUNDLE
EOF
        fi
    else
        warn "Let's Encrypt failed. Falling back to a self signed certificate so the system still runs."
        warn "Fix the DNS token or record, then run this installer again to get a trusted certificate."
        self_signed_cert
    fi
}

# ---------------------------------------------------------------- 7. nginx
configure_nginx() {
    step "Configuring Nginx"
    local version
    version=$(nginx -v 2>&1 | sed -n 's#.*nginx/\([0-9.]*\).*#\1#p')
    # "http2 on" arrived in 1.25.1; Bookworm ships 1.22, Trixie 1.26.
    # Both variables are read by render() through indirect expansion.
    # shellcheck disable=SC2034
    if [[ -n $version ]] && printf '%s\n%s\n' "1.25.1" "$version" | sort -V -C; then
        LISTEN_HTTP2=""
        HTTP2_DIRECTIVE="    http2 on;"
    else
        LISTEN_HTTP2=" http2"
        HTTP2_DIRECTIVE=""
    fi
    info "nginx ${version:-unknown}"

    render "$SCRIPT_DIR/files/nginx-proxy-snippet.conf" | write_file 644 root:root /etc/nginx/snippets/kidnyce-proxy.conf
    # Some installs turn IPv6 off (ipv6.disable=1); Nginx then refuses [::] listeners.
    local strip_v6=(cat)
    if [[ ! -e /proc/net/if_inet6 ]]; then
        strip_v6=(grep -v 'listen \[::\]')
        info "IPv6 is off on this machine, listening on IPv4 only"
    fi
    render "$SCRIPT_DIR/files/nginx-site.conf" | "${strip_v6[@]}" | write_file 644 root:root /etc/nginx/sites-available/kidnyce-karaoke.conf
    run ln -sfn /etc/nginx/sites-available/kidnyce-karaoke.conf /etc/nginx/sites-enabled/kidnyce-karaoke.conf
    # Debian's default site also claims default_server on port 80.
    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        run rm -f /etc/nginx/sites-enabled/default
        info "disabled the stock default site"
    fi
    if ((!DRY_RUN)); then
        nginx -t -q || die "nginx rejected the configuration (see above)"
    fi
    ok "nginx configuration valid"
}

# ---------------------------------------------------------------- 8. ddns
configure_ddns() {
    ((DDNS)) || return 0
    step "Keeping $DOMAIN pointed at this Pi (Cloudflare DNS)"
    printf '%s\n' "$CF_TOKEN" | write_file 600 root:root "$ETC_DIR/cloudflare.token"
    write_file 644 root:root "$ETC_DIR/ddns.env" <<EOF
# Written by deploy/install-pi.sh. Read by kidnyce-karaoke-ddns.
DOMAIN=$DOMAIN
ZONE=$ZONE
ZONE_ID=$CF_ZONE_ID
TOKEN_FILE=$ETC_DIR/cloudflare.token
TTL=60
EOF
    run install -m 644 "$SCRIPT_DIR/files/kidnyce-karaoke-ddns.service" /etc/systemd/system/
    run install -m 644 "$SCRIPT_DIR/files/kidnyce-karaoke-ddns.timer" /etc/systemd/system/
    systemctl_do daemon-reload
    systemctl_do enable kidnyce-karaoke-ddns.timer
    if ((!DRY_RUN && !NO_START)); then
        if /usr/local/sbin/kidnyce-karaoke-ddns; then
            ok "DNS record set"
        else
            warn "could not update the DNS record now; the timer retries every 5 minutes"
        fi
    fi
}

# ---------------------------------------------------------------- 9. firewall
configure_firewall() {
    ((FIREWALL)) || return 0
    step "Installing the firewall"
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        warn "ufw is active, so the nftables firewall was skipped. Allow 80/443 from your LAN in ufw."
        return
    fi
    run install -m 644 "$SCRIPT_DIR/files/firewall.nft" "$ETC_DIR/firewall.nft"
    if ((!DRY_RUN)); then
        nft -c -f "$ETC_DIR/firewall.nft" || die "firewall rules failed validation"
    fi
    run install -m 644 "$SCRIPT_DIR/files/kidnyce-karaoke-firewall.service" /etc/systemd/system/
    systemctl_do daemon-reload
    systemctl_do enable kidnyce-karaoke-firewall.service
    ok "inbound: 80 and 443 and SSH from private networks only"
}

# ---------------------------------------------------------------- 10. kiosk
configure_kiosk() {
    ((KIOSK)) || return 0
    step "Setting up the HDMI screens"
    if ! id "$KIOSK_USER" >/dev/null 2>&1; then
        run useradd --create-home --shell /bin/bash "$KIOSK_USER"
        run passwd --lock "$KIOSK_USER"
    fi
    local group
    for group in video audio render input tty; do
        getent group "$group" >/dev/null && run usermod -a -G "$group" "$KIOSK_USER"
    done
    local home
    home=$(getent passwd "$KIOSK_USER" | cut -d: -f6 || true)
    home=${home:-/home/$KIOSK_USER}

    run install -m 755 -o root -g root "$SCRIPT_DIR/kiosk/kiosk-session.sh" "$PREFIX/kiosk/kiosk-session.sh"
    render "$SCRIPT_DIR/kiosk/bash_profile" | write_file 644 "$KIOSK_USER:$KIOSK_USER" "$home/.bash_profile"
    write_file 644 root:root "$ETC_DIR/kiosk.env" <<EOF
# Written by deploy/install-pi.sh. Change and run: sudo kidnyce-karaoke restart screens
# One screen or two, which port, and swapping them: Settings, Screens, or
# sudo kidnyce-karaoke screens. Those apply within 5 seconds, no restart.
APP_URL=http://127.0.0.1:$PORT
DISPLAY_MODE=$DISPLAY_MODE
# hdmi-singer (the singer screen, or the one screen), hdmi-crowd, headphones
# (3.5 mm jack to your mixer), usb, pulse, or an ALSA PCM
AUDIO_OUT=$AUDIO_OUT
EOF
    render "$SCRIPT_DIR/files/kiosk-autologin.conf" | write_file 644 root:root /etc/systemd/system/getty@tty1.service.d/kidnyce-autologin.conf

    # The screens need the console, not a desktop session.
    if has_systemd && systemctl is-enabled display-manager.service >/dev/null 2>&1; then
        warn "a desktop login (display manager) is enabled. The karaoke screens replace it."
        if confirm "Switch this Pi to boot to the karaoke screens instead of the desktop?"; then
            systemctl_do disable display-manager.service
            systemctl_do set-default multi-user.target
        else
            warn "desktop left enabled; the screens will not start until it is disabled"
        fi
    fi

    if ((FORCE_HDMI)); then
        local cmdline=/boot/firmware/cmdline.txt
        [[ -f $cmdline ]] || cmdline=/boot/cmdline.txt
        if [[ -f $cmdline ]] && ! grep -q "video=HDMI-A-1" "$cmdline"; then
            run cp -n "$cmdline" "$cmdline.kidnyce.bak"
            run sed -i "1 s/\$/ video=HDMI-A-1:${DISPLAY_MODE}@60D video=HDMI-A-2:${DISPLAY_MODE}@60D/" "$cmdline"
            info "both HDMI outputs forced on at $DISPLAY_MODE (backup: $cmdline.kidnyce.bak)"
        fi
    fi
    ok "screens: ${SCREENS:-auto}, audio: $AUDIO_OUT"
}

# ---------------------------------------------------------------- 11. timers
configure_timers() {
    step "Scheduling certificate renewal and yt-dlp updates"
    render "$SCRIPT_DIR/files/kidnyce-karaoke-ytdlp.service" | write_file 644 root:root /etc/systemd/system/kidnyce-karaoke-ytdlp.service
    run install -m 644 "$SCRIPT_DIR/files/kidnyce-karaoke-ytdlp.timer" /etc/systemd/system/
    systemctl_do daemon-reload
    systemctl_do enable kidnyce-karaoke-ytdlp.timer
    if has_systemd && systemctl list-unit-files certbot.timer >/dev/null 2>&1; then
        systemctl_do enable certbot.timer
        ok "certbot.timer renews the certificate automatically"
    fi
}

# ---------------------------------------------------------------- 12. start
start_services() {
    if ((NO_START)) || ! has_systemd || ((DRY_RUN)); then
        step "Not starting services (--no-start, dry run, or no systemd)"
        return
    fi
    step "Starting everything"
    ((FIREWALL)) && systemctl restart kidnyce-karaoke-firewall.service
    systemctl restart kidnyce-karaoke.service
    systemctl enable --now nginx.service >/dev/null
    systemctl reload-or-restart nginx.service
    ((DDNS)) && systemctl start kidnyce-karaoke-ddns.timer
    systemctl start kidnyce-karaoke-ytdlp.timer

    local i
    for i in $(seq 1 90); do
        curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1 && break
        sleep 1
    done
    ((i < 90)) || die "the app did not start. See: journalctl -u kidnyce-karaoke -n 100"
    ok "app is up"
    if curl -fsS --noproxy '*' --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/healthz" >/dev/null 2>&1; then
        ok "https://$DOMAIN answers with a trusted certificate"
    elif curl -fsSk --noproxy '*' --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/healthz" >/dev/null; then
        ok "https://$DOMAIN answers (certificate: $CERT_KIND)"
    else
        die "Nginx is not serving https://$DOMAIN. See: sudo nginx -t; sudo tail /var/log/nginx/error.log"
    fi
    if ((KIOSK)); then
        systemctl daemon-reload
        systemctl restart getty@tty1.service
        ok "screens starting on the HDMI outputs"
    fi
}

# The host's screen choice lives in the app's settings, so it goes in through
# the app once it runs. Only flags given on this run are applied; Settings
# keeps everything else.
apply_screen_choice() {
    [[ -n $SCREENS || $SWAP_SCREENS == 1 ]] || return 0
    if ((DRY_RUN)) || ! curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1; then
        info "app not running, so the screen setup was not applied. Later: sudo kidnyce-karaoke screens ${SCREENS:-auto}"
        return
    fi
    step "Choosing the screen setup"
    if [[ -n $SCREENS ]]; then /usr/local/bin/kidnyce-karaoke screens "$SCREENS" >/dev/null; fi
    if ((SWAP_SCREENS)); then /usr/local/bin/kidnyce-karaoke screens swap on >/dev/null; fi
    ok "screens: ${SCREENS:-as before}$( ((SWAP_SCREENS)) && echo ", singer and room swapped")"
}

# ---------------------------------------------------------------- summary
summary() {
    local code="" url=""
    if ((!DRY_RUN && !NO_START)) && has_systemd; then
        read -r code url < <(curl -fsS "http://127.0.0.1:$PORT/api/display/state" | jq -r '.join | "\(.code) \(.join_url)"') || true
    fi
    if ((DRY_RUN)); then
        printf '\n%s==== Dry run finished: nothing was changed ====%s\n' "$C_BOLD" "$C_OFF"
    else
        printf '\n%s==== Kid Nyce Karaoke is installed ====%s\n' "$C_BOLD" "$C_OFF"
    fi
    echo "  Guests:        https://$DOMAIN  (scan the QR code on the crowd screen)"
    [[ -n $code ]] && echo "  Join code:     $code  ($url)"
    echo "  Certificate:   ${CERT_KIND:-unknown}"
    echo "  Songs folder:  $DATA_DIR/songs"
    echo "  Break music:   $DATA_DIR/music"
    echo "  Control:       kidnyce-karaoke status | doctor | code | new-code | logs"
    if [[ -n $FIRST_PASSWORD ]]; then
        # To the terminal only, never to the install log.
        printf '  Admin password: %s%s%s  (also: sudo kidnyce-karaoke password)\n' "$C_BOLD" "$FIRST_PASSWORD" "$C_OFF" >&3
    fi
    if ((KIOSK)); then
        echo "  Screens:       two TVs: singer on HDMI0 (next to USB C), room on HDMI1. One TV: everything on it."
        echo "                 Change in Settings, Screens, or: sudo kidnyce-karaoke screens"
        echo "                 Reboot once to be sure they come up on their own: sudo reboot"
    fi
    if ((${#WARNINGS[@]})); then
        printf '\n%sWarnings%s\n' "$C_YELLOW" "$C_OFF"
        printf '  - %s\n' "${WARNINGS[@]}"
    fi
}

main() {
    # Keep the real terminal on fd 3 for secrets; everything else is also logged.
    exec 3>&1
    if ((!DRY_RUN)) && [[ $EUID -eq 0 ]]; then
        install -m 600 /dev/null "$LOG_FILE.new" && mv -f "$LOG_FILE.new" "$LOG_FILE"
        exec > >(tee -a "$LOG_FILE") 2>&1
    fi
    printf '%sKid Nyce Karaoke installer %s%s (app %s)\n' "$C_BOLD" "$INSTALLER_VERSION" "$C_OFF" "$APP_VERSION"
    preflight
    read_cloudflare_token
    install_packages
    create_users_and_dirs
    install_deno
    install_app
    obtain_certificate
    configure_app
    configure_nginx
    configure_ddns
    configure_firewall
    configure_kiosk
    configure_timers
    start_services
    apply_screen_choice
    summary
}

# Sourcing the script (for tests) defines everything without running it.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
