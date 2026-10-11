# Deploying Kid Nyce Karaoke on a Raspberry Pi 4B

One script turns a fresh Raspberry Pi OS install into the karaoke box: the app, two
HDMI screens, Nginx with a trusted Let's Encrypt certificate for
`karaoke.djkidnyce.com`, a firewall, and a DNS record that follows the Pi from venue
to venue.

## What you need

| Item | Notes |
| --- | --- |
| Raspberry Pi 4B, 4 GB or 8 GB | 2 GB is refused: two Chromium screens need the RAM. |
| Official 5V 3A USB C supply | Undervoltage throttles the CPU and drops video frames. |
| microSD (32 GB or more, A2) or USB SSD | Songs live on it. An SSD is faster and lasts longer. |
| Two micro HDMI to HDMI cables | HDMI 0 (next to USB C) is the singer screen, HDMI 1 the crowd screen. |
| Cloudflare account with `djkidnyce.com` | For the certificate and the DNS record. See below. |
| Optional: USB audio interface | Cleaner than the 3.5 mm jack for feeding your mixer. |

## 1. Cloudflare (once)

The installer gets the certificate with a DNS challenge, so nothing on the Pi is
exposed to the internet, and it keeps an `A` record for `karaoke.djkidnyce.com`
pointed at the Pi's address on whatever network it joins.

1. Put `djkidnyce.com` on Cloudflare DNS if it is not there yet (Add a site, then
   switch the nameservers at your registrar). Cloudflare copies the existing records,
   so the WordPress site and email keep working. Leave those records as they are.
2. Create an API token: My Profile, API Tokens, Create Token, "Edit zone DNS"
   template. Permissions: `Zone / DNS / Edit`. Zone resources: `Include / Specific
   zone / djkidnyce.com`. Give it an expiry date you will remember to renew.
3. Save the token in a file on the Pi, readable only by you:
   `install -m 600 /dev/null ~/cloudflare.token && nano ~/cloudflare.token`

That token can edit every record in `djkidnyce.com`. Keep the file `600`, never commit
it, and revoke it from the Cloudflare dashboard if the Pi is ever lost.

If your DNS is somewhere else, see "Other DNS hosts" at the end.

## 2. Flash the Pi

Raspberry Pi Imager, Raspberry Pi OS Lite (64 bit). Trixie is current; Bookworm also
works. In the Imager settings set the hostname, your user, WiFi, and an SSH key.
Boot it, then:

```sh
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

The Desktop image works too. The installer offers to switch it to boot straight into
the karaoke screens.

## 3. Install

Copy the release folder to the Pi (`scp -r kidnyce-karaoke pi@<pi-address>:`), then:

```sh
cd ~/kidnyce-karaoke
sudo ./deploy/install-pi.sh --cf-token-file ~/cloudflare.token
```

It takes about 10 minutes on a Pi 4. At the end it prints the admin password (once;
`sudo kidnyce-karaoke password` shows it again) and the join code. Reboot once:
`sudo reboot`. The two screens come up on their own.

Run the same command again any time to update. Songs, settings, play history, the
certificate and the admin password are kept.

### Useful options

```text
--audio usb               player sound to a USB audio interface (default: singer TV over HDMI)
--audio headphones        player sound to the 3.5 mm jack
--display-mode 1280x720   lower resolution if a TV struggles
--force-hdmi              keep both outputs on even if a TV is off at boot
--staging                 trial run against Let's Encrypt staging (untrusted cert)
--dry-run                 print every step, change nothing
--email you@example.com   Let's Encrypt account email (optional)
```

`sudo ./deploy/install-pi.sh --help` lists everything.

## 4. Check it

```sh
kidnyce-karaoke doctor
```

It checks the services, HTTPS through Nginx with a trusted certificate, days left on
the certificate, that `karaoke.djkidnyce.com` resolves to this Pi, Deno and `yt-dlp`,
power and temperature, both screens, and lists the sound devices.

Then from a phone on the same WiFi: scan the QR code on the crowd screen. It should
open `https://karaoke.djkidnyce.com/join/XXXXXX` with a padlock and land on the home
page.

## Gig day

| When | Do |
| --- | --- |
| Arrive | Join the venue WiFi (or plug in Ethernet). The DNS record updates within 5 minutes. Run `kidnyce-karaoke doctor`. |
| Start | Settings, Sessions: start a session named for the night. The join code stays the same until the session changes. |
| Breaks | Home page, Hosting: "Break before next singer", or turn breaks on in Settings, Intermission. |
| Trouble guest | `sudo kidnyce-karaoke new-code`: every phone has to scan again. |
| End | End the session. The code rotates, so last night's phones are locked out. |

Drop songs into `/var/lib/kidnyce-karaoke/songs` and break music into
`/var/lib/kidnyce-karaoke/music` over SFTP. Your login was added to the group that owns
both folders (log out and in once). Then Settings, "Rescan" under Intermission.

## Day to day commands

```text
kidnyce-karaoke status          services, certificate, address, join code
kidnyce-karaoke code            current join code and QR address
sudo kidnyce-karaoke new-code   issue a new join code
sudo kidnyce-karaoke password   show the admin password
sudo kidnyce-karaoke set-password
kidnyce-karaoke doctor
kidnyce-karaoke logs app|nginx|screens|ddns
sudo kidnyce-karaoke restart app|screens|all
sudo kidnyce-karaoke update-ytdlp
sudo kidnyce-karaoke ddns
```

## Venue network notes

Guests must be on the same network as the Pi: `karaoke.djkidnyce.com` points at the
Pi's private address. Two things can get in the way:

1. **Client isolation** ("guest mode" WiFi that blocks devices from seeing each
   other). Phones then cannot reach the Pi at all.
2. **DNS rebinding protection** on some routers, which drops public names that
   resolve to private addresses. The name then fails to resolve on phones.

The reliable fix for both is your own travel router (GL.iNet and similar): it joins
the venue WiFi or Ethernet as its uplink, gives the Pi a fixed address, and broadcasts
your own SSID. Put a DHCP reservation on the Pi and, in the router's DNS settings,
add `karaoke.djkidnyce.com` pointing at it. Guests join your SSID and scan.

## What the installer sets up

| Piece | Where | Notes |
| --- | --- | --- |
| App | `/opt/kidnyce-karaoke/venv`, service `kidnyce-karaoke` | Runs as user `pikaraoke`, loopback only, systemd sandboxing, hash pinned dependencies. |
| Data | `/var/lib/kidnyce-karaoke` | Songs, break music, config, SQLite database. |
| Config | `/etc/kidnyce-karaoke` | `install.env`, `app.env` (extra flags), `kiosk.env` (screens), secrets (mode 600). |
| Nginx | `/etc/nginx/sites-available/kidnyce-karaoke.conf` | TLS 1.2/1.3, HSTS, CSP, rate limits, unknown hosts dropped. |
| Certificate | `/etc/letsencrypt/live/kidnyce-karaoke/` | ECDSA on the NIST P256 curve, renewed by `certbot.timer`, Nginx reloaded after renewal. |
| DNS record | `kidnyce-karaoke-ddns.timer` | Every 5 minutes; only ever publishes a private address. |
| Firewall | `kidnyce-karaoke-firewall.service`, table `inet kidnyce_fw` | Inbound 80/443/22 from private ranges only. |
| Screens | user `kiosk`, autologin on tty1, `/opt/kidnyce-karaoke/kiosk/kiosk-session.sh` | X11 with no window manager, one Chromium per screen, relaunches on crash or hotplug. |
| `yt-dlp` | `kidnyce-karaoke-ytdlp.timer` | Weekly update; YouTube changes often. |
| Deno 2.9.6 | `/opt/kidnyce-karaoke/bin/deno` | Checksum verified. `yt-dlp` needs it for YouTube. |

`sudo ./deploy/uninstall-pi.sh` removes all of it and keeps your songs and settings;
add `--purge` to delete those too.

## Other DNS hosts

* **Route 53:** `--dns-provider route53` with AWS credentials for root. Create the
  `A` record yourself, and use `--no-ddns` (the record will not follow the Pi).
* **Any provider with an API:** `--dns-provider hook --dns-auth-hook /path/script`
  using Certbot's manual hook interface (`CERTBOT_DOMAIN`, `CERTBOT_VALIDATION`).
* **No API at all:** `--dns-provider manual` asks you to add a TXT record by hand. You
  repeat that before every renewal (about every 60 days), so it is only good for trials.
* **Lab only:** `--dns-provider selfsigned`. Phones show a warning.

## Tests

`deploy/tests/` holds what was used to test this before release:

* `sandbox-e2e.sh`: runs the installer on a scratch Debian or Ubuntu machine against
  Pebble (Let's Encrypt's test CA) and checks the result through Nginx: 45 checks
  including a Playwright browser playing a night through.
* `kiosk-xdummy-test.sh`: the screen session on a real Xorg server with two virtual
  outputs.
* `test_ddns_script.py`: the DNS updater against a mock Cloudflare API.

Never run `sandbox-e2e.sh` on your real Pi: it stands up a fake CA.
