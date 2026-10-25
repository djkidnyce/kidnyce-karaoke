#!/usr/bin/env bash
# Removes what deploy/install-pi.sh set up. Songs, break music, settings and
# play history in /var/lib/kidnyce-karaoke stay unless you pass --purge.
# System packages (nginx, ffmpeg, chromium...) are left installed.
set -euo pipefail

PURGE=0
[[ ${1:-} == --purge ]] && PURGE=1
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

ETC_DIR=/etc/kidnyce-karaoke
KIOSK_USER=kiosk
PREFIX=/opt/kidnyce-karaoke
DATA_DIR=/var/lib/kidnyce-karaoke
APP_USER=pikaraoke
if [[ -f $ETC_DIR/install.env ]]; then
    # shellcheck source=/dev/null
    source "$ETC_DIR/install.env"
fi

units=(kidnyce-karaoke.service kidnyce-karaoke-ddns.timer kidnyce-karaoke-ddns.service
    kidnyce-karaoke-ytdlp.timer kidnyce-karaoke-ytdlp.service kidnyce-karaoke-firewall.service
    kidnyce-karaoke-tunnel.service kidnyce-karaoke-netfallback.service)
# The tunnel and its DNS record at Cloudflare, while the token is still here.
if [[ -f $ETC_DIR/tunnel.env && -x /usr/local/sbin/kidnyce-karaoke-tunnel ]]; then
    systemctl stop kidnyce-karaoke-tunnel.service 2>/dev/null || true
    /usr/local/sbin/kidnyce-karaoke-tunnel remove ||
        echo "Could not delete the Cloudflare Tunnel; delete it in the Zero Trust dashboard."
fi
for unit in "${units[@]}"; do
    systemctl disable --now "$unit" 2>/dev/null || true
    rm -f "/etc/systemd/system/$unit"
done
rm -f /etc/systemd/system/getty@tty1.service.d/kidnyce-autologin.conf
rm -rf /etc/systemd/system/kidnyce-karaoke.service.d
rm -f /etc/systemd/system/certbot.service.d/kidnyce-ca.conf
systemctl daemon-reload
nft delete table inet kidnyce_fw 2>/dev/null || true

rm -f /etc/nginx/sites-enabled/kidnyce-karaoke.conf /etc/nginx/sites-available/kidnyce-karaoke.conf \
    /etc/nginx/sites-enabled/kidnyce-karaoke.tunnel.conf /etc/nginx/sites-available/kidnyce-karaoke.tunnel.conf \
    /etc/nginx/snippets/kidnyce-proxy.conf /etc/nginx/snippets/kidnyce-common.conf
if [[ ! -e /etc/nginx/sites-enabled/default && -f /etc/nginx/sites-available/default ]]; then
    ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
fi
if nginx -t -q 2>/dev/null; then systemctl reload nginx 2>/dev/null || true; fi

pkill -f kiosk-session.sh 2>/dev/null || true
rm -f /usr/share/applications/kidnyce-karaoke-{screens,stop,host}.desktop \
    /etc/xdg/autostart/kidnyce-karaoke-screens.desktop \
    /usr/share/icons/hicolor/256x256/apps/kidnyce-karaoke.png /home/*/Desktop/kidnyce-karaoke-*.desktop
pkill -u "$KIOSK_USER" 2>/dev/null || true
rm -f "/home/$KIOSK_USER/.bash_profile"
rm -f /usr/local/bin/kidnyce-karaoke /usr/local/sbin/kidnyce-karaoke-ddns \
    /usr/local/sbin/kidnyce-karaoke-tunnel /usr/local/sbin/kidnyce-karaoke-netfallback
rm -rf "$PREFIX" /var/lib/kidnyce-karaoke-ddns

if ((PURGE)); then
    certbot delete --non-interactive --cert-name kidnyce-karaoke 2>/dev/null || true
    rm -f /etc/letsencrypt/kidnyce-cloudflare.ini
    rm -rf "$ETC_DIR" "$DATA_DIR"
    userdel "$APP_USER" 2>/dev/null || true
    userdel --remove "$KIOSK_USER" 2>/dev/null || true
    echo "Removed everything, including songs and settings."
else
    rm -f "$ETC_DIR/cloudflare.token" "$ETC_DIR/tunnel.token" "$ETC_DIR/tunnel.env" "$ETC_DIR/public.env"
    echo "Removed. Kept $DATA_DIR (songs, music, settings) and $ETC_DIR."
    echo "Use --purge to delete those, the certificate and the users too."
fi
echo "The Cloudflare DNS record for the karaoke host is left in place; delete it in the dashboard if you like."
