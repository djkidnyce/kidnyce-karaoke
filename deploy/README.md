# Deploying Kid Nyce Karaoke on a Raspberry Pi

One script turns a fresh Raspberry Pi OS install into the karaoke box: the app, one
or two HDMI screens, Nginx with a trusted Let's Encrypt certificate for
`karaoke.kidnyce.com`, a firewall, and a DNS record that follows the Pi from venue
to venue.

## What you need

| Item | Notes |
| --- | --- |
| Raspberry Pi 4B, 4 GB or 8 GB | The target. A Pi 5 or Pi 400 works the same. A 2 GB board or a Pi 3 runs one screen for everything. Under 1 GB is refused. |
| Official 5V 3A USB C supply | Undervoltage throttles the CPU and drops video frames. |
| microSD (32 GB or more, A2) or USB SSD | Songs live on it. An SSD is faster and lasts longer. |
| One or two micro HDMI to HDMI cables | With two TVs, HDMI0 (next to USB C) is the singer screen and HDMI1 the room screen. With one TV, either port shows everything. |
| Cloudflare account with `kidnyce.com` | For the certificate and the DNS record. See below. |
| Optional: USB audio interface | Cleaner than the 3.5 mm jack for feeding your mixer. |

## 1. Cloudflare (once)

The installer gets the certificate with a DNS challenge, so nothing on the Pi is
exposed to the internet, and it keeps an `A` record for `karaoke.kidnyce.com`
pointed at the Pi's address on whatever network it joins.

1. Put `kidnyce.com` on Cloudflare DNS if it is not there yet (Add a site, then
   switch the nameservers at your registrar). Cloudflare copies the existing records,
   so the WordPress site and email keep working. Leave those records as they are.
2. Create an API token: My Profile, API Tokens, Create Token, "Edit zone DNS"
   template. Permissions: `Zone / DNS / Edit`. Zone resources: `Include / Specific zone / kidnyce.com`. Give it an expiry date you will remember to renew.
3. Save the token in a file on the Pi, readable only by you:
   `install -m 600 /dev/null ~/cloudflare.token && nano ~/cloudflare.token`

That token can edit every record in `kidnyce.com`. Keep the file `600`, never commit
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

Clone the repo on the Pi and run the installer:

```sh
sudo apt install -y git
git clone https://github.com/djkidnyce/kidnyce-karaoke.git
cd kidnyce-karaoke
sudo ./deploy/install-pi.sh --cf-token-file ~/cloudflare.token
```

A clone carries no prebuilt app, so the installer builds it first, with build tools
pinned by hash in `deploy/build-requirements.lock`. The release tarball works the same
way and skips that step: copy it to the Pi, unpack it, and run the same command inside.

It takes about 10 minutes on a Pi 4. At the end it prints the admin password (once;
`sudo kidnyce-karaoke password` shows it again) and the join code. Reboot once:
`sudo reboot`. The screens come up on their own, or in desktop mode from the Karaoke
Screens icon.

Run the same command again any time to update (`git pull` first on a clone). Songs,
settings, play history, the certificate and the admin password are kept.

### Useful options

```text
--boot desktop            keep the Pi's desktop; open the screens from an icon (see Desktop mode)
--desktop-autostart on    with --boot desktop: open the screens at desktop login
--audio usb               player sound to a USB audio interface (default: the 3.5 mm jack)
--audio hdmi-singer       player sound through the singer TV's HDMI
--screens single          one screen for everything even with two TVs (default: auto)
--screens dual            two screens even on a 2 GB board
--swap-screens            singer screen on HDMI1, room screen on HDMI0
--display-mode 1280x720   lower resolution if a TV struggles
--force-hdmi              keep both outputs on even if a TV is off at boot
--staging                 trial run against Let's Encrypt staging (untrusted cert)
--dry-run                 print every step, change nothing
--email you@example.com   Let's Encrypt account email (optional)
```

`sudo ./deploy/install-pi.sh --help` lists everything.

## Boot to the screens, or keep the desktop

Two ways to run, and you can switch any time:

| Mode | What happens at boot |
| --- | --- |
| Karaoke (default) | The Pi skips the desktop and goes straight to the screens. Best for gigs: nothing to click, and a crash restarts the screens on its own. |
| Desktop | The Pi boots to its normal desktop. The app still starts at boot, so phones work right away. The screens open when you double click **Karaoke Screens** on the desktop. |

Install in desktop mode with `--boot desktop`, or switch later with
`sudo kidnyce-karaoke mode desktop` (and `mode karaoke` to go back). Both reboot. A
rerun of the installer keeps the mode you are in. Desktop mode needs Raspberry Pi OS
with desktop, not Lite. If the installer finds a desktop login and you answer no to
replacing it, you get desktop mode.

Every install puts these in the menu under Sound & Video, and the first two on the
desktop of the user who ran the installer:

| Icon | Does |
| --- | --- |
| Karaoke Screens | Opens the singer and room screens full screen, the same layout as Settings, Screens. A second click does nothing while they are open. |
| Karaoke Host | Opens `https://karaoke.kidnyce.com` in the browser, for the host page and Settings. |
| Close Karaoke Screens | Closes the screens. Alt+F4 on either screen does the same. |

To open the screens at desktop login without clicking, tick Settings, Screens,
"Desktop mode: open the screens when the desktop starts", or run
`sudo kidnyce-karaoke desktop-autostart on`. It is off by default.

In desktop mode the screens use the monitor layout from the desktop's Screen
Configuration instead of setting it themselves. Sound plays through the desktop's
audio (PipeWire), on the output picked in Settings, Sound, and the device volume
there sets that output's volume. Turn off screen blanking in Raspberry Pi
Configuration, Display, so the TVs stay on during a long song.

## Screens: one or two

Settings, Screens shows the board, each HDMI port with the TV plugged into it (its
name and best resolution, read from the TV), and what each one shows. Pick:

| Setup | What you get |
| --- | --- |
| Auto detect (default) | Two TVs plugged in: singer screen and room screen. One TV: everything on it. A board under 4 GB stays on one screen. |
| One screen for everything | Lyrics video full screen, with the QR code, the join code and the next four singers in the corner. A second TV is turned off. Choose which port. |
| Two screens: singer and room | Singer: lyrics, sound, who is next, no QR code. Room: QR code, join code, singing order, a muted copy of the lyrics. "Swap" flips the ports. |

Changes reach the screens within 5 seconds, no restart. Plugging a TV in or out does
the same: the screen session checks every 5 seconds. "Detect again" refreshes the card.

The same from a terminal:

```text
sudo kidnyce-karaoke screens                 board, ports, TVs, what each shows
sudo kidnyce-karaoke screens single          one screen for everything
sudo kidnyce-karaoke screens auto            back to auto detect
sudo kidnyce-karaoke screens port HDMI-2     which port the one screen uses
sudo kidnyce-karaoke screens swap on         singer on HDMI1, room on HDMI0
```

## Sound

Settings, Sound picks where the sound goes and sets the device volume. Both are kept
in the app, so they come back after a reboot. No `alsamixer` or `alsactl store`
needed.

| Output | Where it plays |
| --- | --- |
| Install default | What `--audio` set at install. Out of the box that is the 3.5 mm headphone jack. |
| Singer TV (HDMI) | The TV showing the singer screen, or the one screen. |
| Room TV (HDMI) | The TV showing the room screen. |
| Headphone jack (3.5 mm) | The Pi's analog jack. Use this for a mixer or powered speakers. |
| USB sound card | The first USB card found. Each USB card plugged in also gets its own entry. |

A new output restarts the screens, which takes a few seconds. Device volume is 0 to
100 and applies within 5 seconds without a restart. Leave it empty to keep whatever
level the Pi already has. The player volume under Playback is separate: it scales the
song inside the browser, the device volume sets the jack or card itself. For a mixer
input, set the device volume around 80 and ride the gain on the mixer.

The same from a terminal:

```text
sudo kidnyce-karaoke audio                   current output, volume, sound devices
sudo kidnyce-karaoke audio headphones        3.5 mm jack
sudo kidnyce-karaoke audio usb               first USB sound card
sudo kidnyce-karaoke audio plughw:CARD=Device,DEV=0   one card by its ALSA name
sudo kidnyce-karaoke audio reset             back to the install default
sudo kidnyce-karaoke audio volume 80         device volume, kept after a reboot
sudo kidnyce-karaoke audio volume keep       stop setting the volume
```

`aplay -l` lists the card names. `speaker-test -D plughw:CARD=Headphones,DEV=0 -c 2 -t wav -l 1` plays a test tone on one device.

## 4. Check it

```sh
kidnyce-karaoke doctor
```

It checks the services, HTTPS through Nginx with a trusted certificate, days left on
the certificate, that `karaoke.kidnyce.com` resolves to this Pi, Deno and `yt-dlp`,
power and temperature, which screens are up, and lists the sound devices.

Then from a phone on the same WiFi: scan the QR code on the room screen (or the one
screen). It should
open `https://karaoke.kidnyce.com/join/XXXXXX` with a padlock and land on the home
page.

## Gig day

| When | Do |
| --- | --- |
| Arrive | Join the venue WiFi (or plug in Ethernet). The DNS record updates within 5 minutes. Run `kidnyce-karaoke doctor`. |
| Start | Settings, Sessions: start a session named for the night. Each boot gives a new join code (Settings, Join code, or `code-on-boot off` to keep it). If you skip the session, the first song starts one and keeps the code guests already scanned. |
| Breaks | Home page, Hosting: "Break before next singer", or turn breaks on in Settings, Intermission. |
| Time left | Settings, Queue: "Night ends at". The queue page then shows the time left and puts a line before the songs that may not fit, counting each song's real length, the changeover between singers and break music. "Show guests the time left" puts it on their queue page and the room screen too. |
| Trouble guest | `sudo kidnyce-karaoke new-code`: every phone has to scan again. |
| End | End the session. The code rotates, so last night's phones are locked out. |

Before a gig, fill the library without queueing anything: on Add New, **Save for later**
saves one song; on Charts, pick a decade and **Download the 1980s number ones** saves
every number one of that decade you do not have yet (preview branch). It works one
song at a time and pauses while anyone sings.

Drop songs into `/var/lib/kidnyce-karaoke/songs` and break music into
`/var/lib/kidnyce-karaoke/music` over SFTP. Your login was added to the group that owns
both folders (log out and in once). Then Settings, "Rescan" under Intermission.

The music folder feeds both the break music and the music that plays while the
screens sit idle between songs. Both play it shuffled, subfolders included (MP3, M4A,
AAC, OGG, Opus, WAV, FLAC, MP4). While the folder is empty, the idle screen plays the
one bundled track.

## Releases

Each update comes numbered: `main v0.7` is the tested release, `preview v0.5` the
next features to try. `kidnyce-karaoke status` prints which one is installed, and
`git describe --tags` in the clone says which one you have checked out. The app
version carries the same number: `1.23.0+main.0.7`.

## Commands

Everything below is also in `kidnyce-karaoke help`. Commands that change something
need `sudo`. Most of them have a matching control in Settings, so the terminal is the
backup, not the only way.

### Status and checks

```text
kidnyce-karaoke status          services, certificate, address, join code
kidnyce-karaoke doctor          checks everything and says what is wrong
kidnyce-karaoke logs app        app log (also nginx, screens, ddns)
```

`doctor` is the first thing to run when something looks off. It reads the certificate
from the live HTTPS port, so it works without root.

### Join code

```text
kidnyce-karaoke code                    current join code and QR address
sudo kidnyce-karaoke new-code           new code now, every phone scans again
sudo kidnyce-karaoke code-on-boot on    new code every time the Pi starts (default)
sudo kidnyce-karaoke code-on-boot off   keep the code across reboots
```

In the app: Settings, Join code.

### Admin password

```text
sudo kidnyce-karaoke password           show it
sudo kidnyce-karaoke set-password       choose a new one
```

### Screens

```text
sudo kidnyce-karaoke screens                 board, ports, TVs, what each shows
sudo kidnyce-karaoke screens auto|single|dual
sudo kidnyce-karaoke screens port HDMI-2     port for one screen, or auto
sudo kidnyce-karaoke screens swap on|off     swap the singer and room screens
```

In the app: Settings, Screens. See [Screens: one or two](#screens-one-or-two).

### Sound

```text
sudo kidnyce-karaoke audio                   show the output, volume and devices
sudo kidnyce-karaoke audio OUTPUT            hdmi-singer, hdmi-crowd, headphones, usb, default, or plughw:CARD=X,DEV=0
sudo kidnyce-karaoke audio reset             back to the install default
sudo kidnyce-karaoke audio volume 0-100      device volume, kept after a reboot
sudo kidnyce-karaoke audio volume keep       leave the volume alone
```

In the app: Settings, Sound. See [Sound](#sound).

### WiFi at a venue

```text
sudo kidnyce-karaoke wifi
```

Opens the NetworkManager menu on the console. Pick the venue network, enter its
password, and back out. The command then prints the new LAN address and updates the
DNS record, so phones can use the name right away. Connect a keyboard to the Pi, or
SSH in over Ethernet.

### Desktop and karaoke mode

```text
sudo kidnyce-karaoke mode                    which mode the Pi boots in
sudo kidnyce-karaoke mode desktop            boot to the desktop, screens from the icon (reboots)
sudo kidnyce-karaoke mode karaoke            boot straight to the screens (reboots)
sudo kidnyce-karaoke desktop-autostart on    desktop mode: open the screens at login
sudo kidnyce-karaoke desktop-autostart off   desktop mode: only from the icon (default)
```

The app keeps running in both modes, so phones still work. Desktop mode also helps
with setup work that needs the full desktop, such as the desktop WiFi menu or
Bluetooth. It needs Raspberry Pi OS with desktop, not Lite. On Lite, use `wifi` above.
See [Boot to the screens, or keep the desktop](#boot-to-the-screens-or-keep-the-desktop).

### Preview features

```text
sudo kidnyce-karaoke tunnel                  tunnel status at Cloudflare
sudo kidnyce-karaoke tunnel http2|auto       for venues that block UDP 7844
sudo kidnyce-karaoke join-from internet      QR code works on cellular (needs the tunnel)
sudo kidnyce-karaoke join-from venue         QR code on the venue network only
sudo kidnyce-karaoke net-fallback on|off     desktop when there is no network at boot
```

### Services and updates

```text
sudo kidnyce-karaoke restart app|screens|all
sudo kidnyce-karaoke update-ytdlp       pull the newest yt-dlp now (also runs every Monday)
sudo kidnyce-karaoke ddns               update the DNS record now (also every 5 minutes)
```

### Updating the app

```sh
cd ~/kidnyce-karaoke && git pull
sudo ./deploy/install-pi.sh --domain karaoke.kidnyce.com --cf-token-file ~/cloudflare.token
```

A rerun keeps the admin password, join code, songs, settings and sound choice. Pass
`--audio` only to change the install default.

## Venue network notes

Guests must be on the same network as the Pi: `karaoke.kidnyce.com` points at the
Pi's private address. Two things can get in the way:

1. **Client isolation** ("guest mode" WiFi that blocks devices from seeing each
   other). Phones then cannot reach the Pi at all.
2. **DNS rebinding protection** on some routers, which drops public names that
   resolve to private addresses. The name then fails to resolve on phones.

The reliable fix for both is your own travel router (GL.iNet and similar): it joins
the venue WiFi or Ethernet as its uplink, gives the Pi a fixed address, and broadcasts
your own SSID. Put a DHCP reservation on the Pi and, in the router's DNS settings,
add `karaoke.kidnyce.com` pointing at it. Guests join your SSID and scan.

## Preview: guests on cellular data (Cloudflare Tunnel)

Off unless you turn it on. It fixes both venue problems above, and lets guests join
without touching the venue WiFi at all.

How it works: `cloudflared` on the Pi dials out to Cloudflare, and Cloudflare sends
one internet name, such as `karaoke-test.kidnyce.com`, down that connection to the
Pi. Nothing is opened on the venue router or the Pi's firewall, so it works behind
any router, a phone hotspot, or carrier NAT. The Pi still needs some internet: venue
WiFi, Ethernet, or a hotspot.

What stays the same: `karaoke.kidnyce.com`, its DNS record, its certificate and the QR
code. The tunnel name is separate, so you can test it without touching what works.

### 1. Let the token manage tunnels

Edit the Cloudflare API token you already use and add a permission:
**Account, Cloudflare Tunnel, Edit**. Keep **Zone, DNS, Edit** on `kidnyce.com`.

### 2. Install with a test name

```sh
sudo ./deploy/install-pi.sh --domain karaoke.kidnyce.com --cf-token-file ~/cloudflare.token \
    --tunnel karaoke-test.kidnyce.com
```

The installer adds Cloudflare's signed apt repository and installs `cloudflared`. It
creates a tunnel named `kidnyce-karaoke-karaoke-test` and routes only that name to
the Pi. It adds a proxied CNAME for the name, and starts the `kidnyce-karaoke-tunnel`
service. It refuses a name that already has other DNS records.

### 3. Test from a phone on cellular

Turn WiFi off on a phone. Open `https://karaoke-test.kidnyce.com/join`, type the join
code from the screen, and add a song. Check `sudo kidnyce-karaoke tunnel`: it should
say healthy with 4 connections.

### 4. Put it in the QR code

Settings, Join code, "QR code works from anywhere", or
`sudo kidnyce-karaoke join-from internet`. The QR code and the address on the room
screen switch to the tunnel name within seconds. `join-from venue` switches back.

### Host controls stay local

Host login, Settings and every host action refuse requests that came through the
tunnel, even with the right password. Run the night from the venue network, or from
the Pi's address in small print on the screens. A leaked admin password is useless
from the internet.

### Threat model

| Risk | What stops it |
| --- | --- |
| Strangers finding the URL | The join code gate. Without tonight's code a phone sees only the join page. |
| Guessing the code | Nginx limits each phone to 10 join attempts a minute, and the app locks out repeated failures. Each phone counts on its own, from Cloudflare's CF-Connecting-IP. |
| Taking over the host controls | Refused from the tunnel: Nginx marks those requests, the app never treats them as the host. A client cannot set or clear the mark itself. |
| Reaching the Pi directly | Nothing listens for the internet. The tunnel's Nginx server answers on 127.0.0.1:8081 only, and the Pi's public address stays hidden behind Cloudflare. |
| The tunnel token | Root only on disk (`/etc/kidnyce-karaoke/tunnel.token`), handed to a throwaway service user by systemd. It can only run this tunnel, not edit your account. |
| Floods | Cloudflare's edge in front, plus the same Nginx rate limits as on the venue network. |

### Venue networks that block it

`cloudflared` needs outbound port 7844: UDP for QUIC, TCP for HTTP/2. If a venue
blocks UDP, run `sudo kidnyce-karaoke tunnel http2`, and `tunnel auto` to go back.

### Turn it off

```sh
sudo ./deploy/install-pi.sh --domain karaoke.kidnyce.com --cf-token-file ~/cloudflare.token --no-tunnel
```

That stops `cloudflared` and deletes the tunnel and its DNS record at Cloudflare. It
also removes the tunnel's Nginx server and puts the QR code back on the venue name.

## Preview: show the desktop when there is no network

Off unless you turn it on, and only in karaoke mode. At boot the Pi waits up to 45
seconds for a network address, meaning Ethernet or a known WiFi network with an
answer from its router. If none comes, it shows the Raspberry Pi desktop so you can
pick a WiFi network with the mouse. Once it has an address for two checks in a row, it
waits 10 seconds, closes the desktop and starts the screens.

```sh
sudo kidnyce-karaoke net-fallback on         # or install with --net-fallback on
sudo kidnyce-karaoke net-fallback off
```

`--net-wait 90` changes the wait. Raspberry Pi OS Lite has no desktop, so there it
only logs and the screens start anyway; use `sudo kidnyce-karaoke wifi`. In desktop
mode with "open the screens when the desktop starts" on, the screens wait for a
network address before covering the desktop.

The Pi's address shows in small print in a corner of the screens, or "No network"
when it has none. Turn it off under Settings, Splash screen.

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
| Screens | user `kiosk`, autologin on tty1, `/opt/kidnyce-karaoke/kiosk/kiosk-session.sh` | X11 with no window manager, one Chromium per screen. Every 5 seconds it reports the connected ports to the app and gets back which page goes where. Relaunches on a crash, a hotplug or a change in Settings. In desktop mode the same script runs in the desktop session instead, started by the icon or `/etc/xdg/autostart`. |
| Launchers | `/usr/share/applications/kidnyce-karaoke-*.desktop`, `~/Desktop` | Karaoke Screens, Karaoke Host, Close Karaoke Screens. |
| `yt-dlp` | `kidnyce-karaoke-ytdlp.timer` | Weekly update; YouTube changes often. |
| Deno 2.9.6 | `/opt/kidnyce-karaoke/bin/deno` | Checksum verified. `yt-dlp` needs it for YouTube. |

`sudo ./deploy/uninstall-pi.sh` removes all of it and keeps your songs and settings;
add `--purge` to delete those too.

## Other DNS hosts

- **Route 53:** `--dns-provider route53` with AWS credentials for root. Create the
  `A` record yourself, and use `--no-ddns` (the record will not follow the Pi).
- **Any provider with an API:** `--dns-provider hook --dns-auth-hook /path/script`
  using Certbot's manual hook interface (`CERTBOT_DOMAIN`, `CERTBOT_VALIDATION`).
- **No API at all:** `--dns-provider manual` asks you to add a TXT record by hand. You
  repeat that before every renewal (about every 60 days), so it is only good for trials.
- **Lab only:** `--dns-provider selfsigned`. Phones show a warning.

## Tests

`deploy/tests/` holds what was used to test this before release:

- `sandbox-e2e.sh`: runs the installer on a scratch Debian or Ubuntu machine against
  Pebble (Let's Encrypt's test CA) and checks the result through Nginx: 45 checks
  including a Playwright browser playing a night through.
- `kiosk-xdummy-test.sh`: the screen session on a real Xorg server with virtual
  outputs: two screens, switching to one screen, moving it to the other port, two
  screens swapped, and a single TV. `sandbox-e2e.sh` runs it at the end when `CHROME`
  is set.
- `test_ddns_script.py`: the DNS updater against a mock Cloudflare API.

Never run `sandbox-e2e.sh` on your real Pi: it stands up a fake CA.
