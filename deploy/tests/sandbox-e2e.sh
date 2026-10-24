#!/usr/bin/env bash
# End to end test of deploy/install-pi.sh on a scratch Debian or Ubuntu machine
# (never on your real Pi). It stands up Pebble, Let's Encrypt's test ACME
# server, plus its DNS test server, runs the installer against them with the
# hook DNS provider, then exercises the result through Nginx with real TLS.
#
# Needs: root, the pebble and pebble-challtestsrv binaries on PATH, and the
# pebble source tree (for its test CA) at $PEBBLE_SRC.
#
#   sudo PEBBLE_SRC=/tmp/pebble-src deploy/tests/sandbox-e2e.sh
set -Eeuo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PEBBLE_SRC=${PEBBLE_SRC:?set PEBBLE_SRC to the pebble source tree}
DOMAIN=karaoke.djkidnyce.com
WORK=$(mktemp -d)
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$*"; }
check() { local name=$1; shift; if "$@"; then pass "$name"; else fail "$name"; fi; }

cleanup() {
    # The background jobs are subshells, so kill what they started as well, or
    # the next run's Pebble cannot bind and talks to this run's instead.
    [[ -n ${APP_PID:-} ]] && kill "$APP_PID" 2>/dev/null
    [[ -n ${PEBBLE_PID:-} ]] && kill "$PEBBLE_PID" 2>/dev/null
    [[ -n ${DNS_PID:-} ]] && kill "$DNS_PID" 2>/dev/null
    pkill -x pebble 2>/dev/null || true
    pkill -x pebble-challtestsrv 2>/dev/null || true
    pkill -f "^${PREFIX:-/opt/kidnyce-karaoke}/venv/bin/python" 2>/dev/null || true
    nginx -s stop 2>/dev/null || true
    sed -i '/# kidnyce-e2e$/d' /etc/hosts
}
trap cleanup EXIT

echo "== Making a CA for Pebble's own HTTPS listener"
# Pebble ships a test certificate without an Authority Key Identifier, which
# Python 3.13's strict verification rejects, so make a proper one.
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 -subj "/CN=Kidnyce Test ACME CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" -keyout "$WORK/acme-ca.key" -out "$WORK/acme-ca.pem" 2>/dev/null
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=localhost" \
    -keyout "$WORK/acme.key" -out "$WORK/acme.csr" 2>/dev/null
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nauthorityKeyIdentifier=keyid\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' >"$WORK/acme.ext"
openssl x509 -req -in "$WORK/acme.csr" -CA "$WORK/acme-ca.pem" -CAkey "$WORK/acme-ca.key" -CAcreateserial \
    -days 30 -extfile "$WORK/acme.ext" -out "$WORK/acme.pem" 2>/dev/null
jq --arg c "$WORK/acme.pem" --arg k "$WORK/acme.key" '.pebble.certificate = $c | .pebble.privateKey = $k' \
    "$PEBBLE_SRC/test/config/pebble-config.json" >"$WORK/pebble-config.json"
ACME_CA="$WORK/acme-ca.pem"

echo "== Starting Pebble and its DNS test server"
pkill -x pebble 2>/dev/null || true
pkill -x pebble-challtestsrv 2>/dev/null || true
sleep 1
pebble-challtestsrv -defaultIPv4 127.0.0.1 -defaultIPv6 "" -dns01 :8053 -http01 "" -https01 "" \
    -tlsalpn01 "" -doh "" -management :8055 >"$WORK/challtestsrv.log" 2>&1 &
DNS_PID=$!
(cd "$PEBBLE_SRC" && PEBBLE_VA_NOSLEEP=1 PEBBLE_WFE_NONCEREJECT=0 \
    pebble -config "$WORK/pebble-config.json" -dnsserver 127.0.0.1:8053 >"$WORK/pebble.log" 2>&1) &
PEBBLE_PID=$!
for _ in $(seq 1 30); do curl -s --cacert "$ACME_CA" https://127.0.0.1:14000/dir >/dev/null && break; sleep 0.5; done

cat >"$WORK/auth-hook" <<'EOF'
#!/bin/sh
curl -s -X POST -d "{\"host\":\"_acme-challenge.${CERTBOT_DOMAIN}.\",\"value\":\"${CERTBOT_VALIDATION}\"}" http://127.0.0.1:8055/set-txt
EOF
cat >"$WORK/cleanup-hook" <<'EOF'
#!/bin/sh
curl -s -X POST -d "{\"host\":\"_acme-challenge.${CERTBOT_DOMAIN}.\"}" http://127.0.0.1:8055/clear-txt
EOF
chmod +x "$WORK/auth-hook" "$WORK/cleanup-hook"

# Pebble makes a new root on every start, so a certificate from an earlier test
# run would not verify. Start clean.
certbot delete --non-interactive --cert-name kidnyce-karaoke >/dev/null 2>&1 || true
# Pebble also forgets ACME accounts when it restarts.
rm -rf /etc/letsencrypt/accounts/127.0.0.1:14000

echo "== Running the installer"
"$ROOT/deploy/install-pi.sh" --yes --skip-hw-check --no-kiosk --no-ddns \
    --dns-provider hook --dns-auth-hook "$WORK/auth-hook" --dns-cleanup-hook "$WORK/cleanup-hook" \
    --acme-server https://127.0.0.1:14000/dir --acme-ca-bundle "$ACME_CA" \
    3>&1 | tee "$WORK/install.out"

echo "== Starting the services by hand (no systemd here)"
# shellcheck source=/dev/null
source /etc/kidnyce-karaoke/install.env
nginx -s stop 2>/dev/null || true
pkill -f "^$PREFIX/venv/bin/python" 2>/dev/null || true
sleep 1
# Two short test songs (color bars and a tone) so a night can be played through.
SONG_A="$DATA_DIR/songs/Test Song A---aaaaaaaaaaa.mp4"
SONG_B="$DATA_DIR/songs/Test Song B---bbbbbbbbbbb.mp4"
for song in "$SONG_A" "$SONG_B"; do
    [[ -f $song ]] || ffmpeg -loglevel error -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 \
        -t 12 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$song"
done
chown "$APP_USER:$APP_USER" "$SONG_A" "$SONG_B"
mapfile -t EXEC < <(sed -n '/^ExecStart=/,/PIKARAOKE_EXTRA_ARGS/p' /etc/systemd/system/kidnyce-karaoke.service |
    sed 's/^ExecStart=//; s/\\$//; /PIKARAOKE_EXTRA_ARGS/d' | xargs -n1)
(cd "$DATA_DIR" && runuser -u "$APP_USER" -- env HOME="$DATA_DIR" PATH="$PREFIX/bin:$PREFIX/venv/bin:/usr/bin:/bin" \
    "${EXEC[@]}" >"$WORK/app.log" 2>&1) &
APP_PID=$!
nginx -t -q
nginx
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/api/healthz" >/dev/null && break; sleep 1; done

curl -sk https://127.0.0.1:15000/roots/0 >"$WORK/root.pem"
curl -sk https://127.0.0.1:15000/intermediates/0 >"$WORK/intermediate.pem"
cat "$WORK/root.pem" >"$WORK/trust.pem"
# A build machine may route outbound HTTPS through a proxy, which would
# swallow these requests to our own Nginx.
export NO_PROXY="$DOMAIN,127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}" no_proxy="$DOMAIN,127.0.0.1,localhost${no_proxy:+,$no_proxy}"
H=(--resolve "$DOMAIN:443:127.0.0.1" --resolve "$DOMAIN:80:127.0.0.1" --cacert "$WORK/trust.pem")
U="https://$DOMAIN"

echo "== Checks"
check "app answers on loopback" curl -fs "http://127.0.0.1:$PORT/api/healthz"
check "TLS chain verifies to the ACME root" curl -fs "${H[@]}" "$U/api/healthz"
check "certificate names $DOMAIN" bash -c "openssl x509 -in '$CERT' -noout -ext subjectAltName | grep -q 'DNS:$DOMAIN'"
check "ECDSA key" bash -c "openssl x509 -in '$CERT' -noout -text | grep -q 'id-ecPublicKey'"
check "app is not reachable on the LAN address" bash -c "! curl -fs --max-time 2 http://\$(hostname -I | awk '{print \$1}'):$PORT/api/healthz"
check "HTTP redirects to HTTPS (308)" bash -c "curl -s -o /dev/null -w '%{http_code}' ${H[*]} http://$DOMAIN/ | grep -qx 308"
check "unknown Host header is dropped" bash -c "! curl -sk --max-time 3 -H 'Host: evil.example' https://127.0.0.1/ -o /dev/null"
check "TLS 1.1 is refused" bash -c "! openssl s_client -connect 127.0.0.1:443 -servername $DOMAIN -tls1_1 </dev/null >/dev/null 2>&1"
check "TLS 1.3 works" bash -c "openssl s_client -connect 127.0.0.1:443 -servername $DOMAIN -tls1_3 </dev/null 2>/dev/null | grep -q 'TLSv1.3'"

headers=$(curl -sI "${H[@]}" "$U/join" || true)
for h in strict-transport-security content-security-policy x-content-type-options x-frame-options referrer-policy permissions-policy; do
    check "header $h" grep -qi "^$h:" <<<"$headers"
done
check "no server version leak" bash -c "! grep -qi '^server: nginx/' <<<\"$headers\""

check "guest without code goes to /join" bash -c "curl -s -o /dev/null -w '%{redirect_url}' ${H[*]} $U/ | grep -q '/join$'"
check "API without code is 401" bash -c "curl -s -o /dev/null -w '%{http_code}' ${H[*]} $U/api/get_queue | grep -qx 401"
check "crowd screen state hidden from strangers" bash -c "curl -s -o /dev/null -w '%{http_code}' ${H[*]} $U/api/display/state | grep -qx 401"
CODE=$(curl -fs "http://127.0.0.1:$PORT/api/display/state" | jq -r .join.code || true)
JOIN_URL=$(curl -fs "http://127.0.0.1:$PORT/api/display/state" | jq -r .join.join_url || true)
check "QR address is https://$DOMAIN/join/<code>" test "$JOIN_URL" = "$U/join/$CODE"
jar="$WORK/cookies"
curl -s -c "$jar" -o /dev/null -D "$WORK/join.headers" "${H[@]}" "$U/join/$CODE" || true
check "joining sets a Secure HttpOnly cookie" bash -c "grep -i '^set-cookie:' '$WORK/join.headers' | grep -qi 'secure' && grep -i '^set-cookie:' '$WORK/join.headers' | grep -qi 'httponly'"
check "joined guest sees the home page" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} $U/ | grep -qx 200"
check "joined guest can call the API" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} $U/api/get_queue | grep -qx 200"
check "crowd screen state hidden from joined guests" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} $U/api/display/state | grep -qx 404"
check "crowd page hidden from joined guests" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} $U/crowd | grep -qx 404"
check "crowd page served to the Pi itself" bash -c "curl -fs http://127.0.0.1:$PORT/crowd | grep -q crowd.js"
check "screen layout hidden from joined guests" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} '$U/api/display/layout?connected=HDMI-9' | grep -qx 404"
check "screen hardware is host only" bash -c "curl -s -b '$jar' -o /dev/null -w '%{http_code}' ${H[*]} $U/api/display/hardware | grep -qx 403"
check "the Pi gets its screen layout" bash -c "curl -fs 'http://127.0.0.1:$PORT/api/display/layout?connected=HDMI-1,HDMI-2' | jq -e '.signature == \"singer=HDMI-1 crowd=HDMI-2\"' >/dev/null"
check "charts page loads" bash -c "curl -s -b '$jar' ${H[*]} $U/charts | grep -q 'Number 1 hits'"
check "number ones API" bash -c "curl -s -b '$jar' ${H[*]} '$U/api/charts/number_ones?q=hey%20jude' | jq -e '.songs[0].artist == \"The Beatles\"' >/dev/null"


echo "== Socket.IO through Nginx (polling and websocket)"
# The Python client resolves names itself, so give it the name the way a
# venue DNS record would.
echo "127.0.0.1 $DOMAIN # kidnyce-e2e" >>/etc/hosts
# A small client environment with websocket support, apart from the app's venv.
python3 -m venv "$WORK/testenv"
"$WORK/testenv/bin/pip" install -q "python-socketio[client]==5.16.2" "websocket-client==1.9.0" "requests==2.34.2"
TURNED_AWAY_BEFORE=$(grep -c "turned away" "$WORK/app.log" || true)
# No proxy for these: the websocket client reads proxy settings from the environment.
SOCKET_RESULT=$(env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy -u ALL_PROXY -u all_proxy \
    "$WORK/testenv/bin/python" -I - "$U" "$jar" "$WORK/trust.pem" <<'EOF'
import sys
import requests, socketio
url, jar_path, ca = sys.argv[1:]
results = []
for transports in (["polling"], ["websocket"]):
    for joined in (False, True):
        session = requests.Session()
        session.trust_env = False  # ignore any build machine proxy
        session.verify = ca
        if joined:
            # curl marks HttpOnly cookies with a "#HttpOnly_" prefix, which
            # Python's Netscape cookie reader would skip as a comment.
            for line in open(jar_path):
                line = line.strip()
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif not line or line.startswith("#"):
                    continue
                domain, _, path, secure, _, name, value = line.split("\t")
                session.cookies.set(name, value, domain=domain, path=path, secure=secure == "TRUE")
        client = socketio.Client(http_session=session, ssl_verify=ca)
        try:
            client.connect(url, transports=transports, wait_timeout=5)
            outcome = "connected" if client.connected else "not-connected"
            client.disconnect()
        except socketio.exceptions.ConnectionError as e:
            # A refusal by the app comes back as a failed namespace connect. Anything
            # else (DNS, TLS, proxy) is a broken test, not a refusal.
            outcome = "refused" if "namespace" in str(e).lower() else f"error({e})"
        results.append(f"{transports[0]}:{'joined' if joined else 'stranger'}={outcome}")
print(" ".join(results))
EOF
)
echo "  $SOCKET_RESULT"
check "stranger socket refused (polling)" grep -q "polling:stranger=refused" <<<"$SOCKET_RESULT"
check "joined socket connects (polling)" grep -q "polling:joined=connected" <<<"$SOCKET_RESULT"
check "stranger socket refused (websocket)" grep -q "websocket:stranger=refused" <<<"$SOCKET_RESULT"
check "joined socket connects (websocket upgrade)" grep -q "websocket:joined=connected" <<<"$SOCKET_RESULT"
TURNED_AWAY_AFTER=$(grep -c "turned away" "$WORK/app.log" || true)
check "the app itself refused both stranger sockets" test $((TURNED_AWAY_AFTER - TURNED_AWAY_BEFORE)) -ge 2
check "websocket requests reached Nginx" grep -q "transport=websocket" /var/log/nginx/access.log

echo "== A night played through in a real browser (Playwright Chromium)"
if python3 -c "import playwright" 2>/dev/null; then
    if env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy python3 "$HERE/browser_checks.py" \
        "$DOMAIN" "$PORT" "$CODE" "$(cat /etc/kidnyce-karaoke/admin-password)" "$SONG_A" "$SONG_B" "$WORK/shots" \
        | tee "$WORK/browser.out"; then
        pass "browser checks ($(grep -c '  PASS' "$WORK/browser.out") passed)"
    else
        fail "browser checks: $(grep '  FAIL' "$WORK/browser.out" | tr '\n' ';')"
    fi
else
    echo "  (skipped: Python Playwright not installed)"
fi

echo "== Rotation"
PW=$(cat /etc/kidnyce-karaoke/admin-password)
admin_jar="$WORK/admin"
check "host logs in through Nginx" bash -c "jq -n --arg p '$PW' '{admin_password:\$p}' | curl -fs -c '$admin_jar' ${H[*]} -H 'Content-Type: application/json' --data-binary @- $U/api/auth | jq -e .authenticated >/dev/null"
NEW=$(curl -fs -b "$admin_jar" "${H[@]}" -X POST "$U/api/join_code/rotate" | jq -r .code || true)
check "host rotates the code" test -n "$NEW" -a "$NEW" != "$CODE"
check "old guest cookie is locked out" bash -c "curl -s -b '$jar' -o /dev/null -w '%{redirect_url}' ${H[*]} $U/ | grep -q '/join$'"
check "QR now carries the new code" bash -c "curl -fs http://127.0.0.1:$PORT/api/display/state | jq -e '.join.code == \"$NEW\"' >/dev/null"

echo "== Guessing the code (last, since it rate limits this machine's address)"
codes=$(for _ in $(seq 1 12); do curl -s -o /dev/null -w '%{http_code}\n' "${H[@]}" -X POST -H 'Content-Type: application/json' -d '{"code":"ZZZZZZ"}' "$U/api/join"; done | sort | uniq -c | tr '\n' ' ')
check "join guessing hits 429 ($codes)" grep -q 429 <<<"$codes"
yt=$(for _ in $(seq 1 16); do curl -s -o /dev/null -w '%{http_code}\n' -b "$admin_jar" "${H[@]}" "$U/search"; done | sort | uniq -c | tr '\n' ' ')
check "YouTube search is rate limited per phone ($yt)" grep -q 429 <<<"$yt"

echo "== Renewal"
# A real forced renewal against Pebble (a dry run would switch to Let's Encrypt
# staging). Proves the saved renewal config, DNS hooks and key settings work.
SERIAL_BEFORE=$(openssl x509 -in "$CERT" -noout -serial)
env REQUESTS_CA_BUNDLE="$ACME_CA" certbot renew --force-renewal --no-random-sleep-on-renew --cert-name kidnyce-karaoke -q || true
check "certbot renews with the saved settings" test "$(openssl x509 -in "$CERT" -noout -serial)" != "$SERIAL_BEFORE"
check "renewed key is still ECDSA" bash -c "openssl x509 -in '$CERT' -noout -text | grep -q id-ecPublicKey"
check "renewal config keeps the reload hook" grep -q "renew_hook = systemctl reload nginx" /etc/letsencrypt/renewal/kidnyce-karaoke.conf

echo "== Firewall rules load"
check "kidnyce-karaoke screens shows the board" bash -c "kidnyce-karaoke screens | grep -q '^Board:'"
check "kidnyce-karaoke screens single switches" bash -c "kidnyce-karaoke screens single | grep -q 'Setup:  single'"
check "kidnyce-karaoke screens swap on" bash -c "kidnyce-karaoke screens swap on | grep -q 'swap: on'"
kidnyce-karaoke screens auto >/dev/null
kidnyce-karaoke screens swap off >/dev/null

check "nftables table loads and unloads" bash -c "nft -f /etc/kidnyce-karaoke/firewall.nft && nft list table inet kidnyce_fw >/dev/null && nft delete table inet kidnyce_fw"

echo "== Idempotent re-run, from a git clone (no prebuilt wheel)"
# The tracked files as a clone has them: no dist/, so the installer builds the wheel.
CLONE="$WORK/clone"
mkdir -p "$CLONE"
git -C "$ROOT" ls-files -co --exclude-standard | grep -v '^dist/' | (cd "$ROOT" && tar cf - -T -) | tar xf - -C "$CLONE"
check "second install run succeeds and keeps the password" bash -c "
    '$CLONE/deploy/install-pi.sh' --yes --skip-hw-check --no-kiosk --no-ddns --dns-provider hook \
        --dns-auth-hook '$WORK/auth-hook' --dns-cleanup-hook '$WORK/cleanup-hook' \
        --acme-server https://127.0.0.1:14000/dir --acme-ca-bundle '$ACME_CA' \
        --screens dual \
        3>/dev/null >'$WORK/install2.out' 2>&1 && test \"\$(cat /etc/kidnyce-karaoke/admin-password)\" = '$PW'"
check "installer --screens dual reaches the app" bash -c "kidnyce-karaoke screens | grep -q 'Setup:  dual'"
check "a git clone builds and installs the app wheel" bash -c "grep -q 'built pikaraoke-' '$WORK/install2.out' && test -f '$CLONE'/dist/pikaraoke-*.whl"
kidnyce-karaoke screens auto >/dev/null

echo "== Screens on Xorg with virtual outputs"
if [[ -n ${CHROME:-} ]] && command -v Xorg >/dev/null && [[ -f /usr/lib/xorg/modules/drivers/dummy_drv.so ]]; then
    if "$HERE/kiosk-xdummy-test.sh" | tee "$WORK/kiosk.out"; then
        pass "kiosk screens ($(grep -c '  PASS' "$WORK/kiosk.out") passed)"
    else
        fail "kiosk screens: $(grep '  FAIL' "$WORK/kiosk.out" | tr '\n' ';')"
    fi
else
    echo "  (skipped: needs CHROME and Xorg with the dummy driver)"
fi

echo
echo "Passed: $PASS  Failed: $FAIL"
echo "Logs: $WORK"
((FAIL == 0))
