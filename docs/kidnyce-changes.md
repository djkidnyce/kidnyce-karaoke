# What this fork changes

Kid Nyce Karaoke is [PiKaraoke](https://github.com/vicwomg/pikaraoke) with additions
for running gigs from a Raspberry Pi. This page lists what differs from upstream, what
runs where, and how upstream updates come in. The Pi install guide is
[deploy/README.md](../deploy/README.md).

## Supported platforms

The app itself stays as portable as upstream. The installer, the screens and the
network pieces are for the Pi.

| Platform | App (guest and host pages, player, downloads) | Installer, kiosk screens, Nginx, firewall, tunnel |
| --- | --- | --- |
| Raspberry Pi 4, 4 GB or 8 GB, Raspberry Pi OS 64 bit (Trixie or Bookworm) | Yes. The main target, tested every release. | Yes. Two screens on 4 GB and up. |
| Raspberry Pi 5 or Pi 400 | Yes | Yes, same as the Pi 4 |
| Raspberry Pi 4, 2 GB | Yes | Yes, one screen for everything by default |
| Raspberry Pi 3 | Works with an overclock | Yes, one screen for everything |
| Linux (Debian, Ubuntu, others) | Yes, as upstream: `pikaraoke` from the wheel | The installer runs on Debian and Ubuntu with `--skip-hw-check` (used for testing) |
| macOS | Yes, as upstream | No |
| Windows | Yes, as upstream | No |

What keeps the app portable: every hardware probe the fork added (board model, RAM,
HDMI ports, sound cards) reads Linux files only if they are there and reports "unknown"
otherwise; the header logo lives in the per user data folder upstream already uses
(`~/.pikaraoke`, or `%APPDATA%\pikaraoke` on Windows); download priority uses
`psutil`, with the Windows priority classes on Windows. The CI smoke test installs and
launches the app on Ubuntu, macOS and Windows.

## Features added

| Area | What it does | Where |
| --- | --- | --- |
| Pi installer | One command: app, Nginx with TLS, Let's Encrypt by DNS challenge, firewall, sandboxed service, DNS record that follows the Pi, screens. Reruns update in place. | `deploy/install-pi.sh` |
| Local network only | No domain or Cloudflare token: phones join at `http://<the Pi's address>`, and the QR code follows the Pi to any network. | `--local-only`; `--url http://{ip}` in the app |
| Your own domain | A first install picks the domain from the Cloudflare token's zones; guests come in through a tunnel or on the venue network; the host gets an optional login name. | `deploy/install-pi.sh` (`--domain`, `--guests`, `--host-name`) |
| One screen or two | Singer lyrics on one TV, QR code, join code and singing order for the room on the other; one TV shows everything. Board and port detection. | `pikaraoke/lib/display_hardware.py`, `deploy/kiosk/` |
| Join code per night | A 6 character code in the QR code; a new session issues a new one and drops old phones. | `pikaraoke/lib/join_codes.py`, `guest_gate.py` |
| Break music | Songs between singers, planned to fit the break length. | `pikaraoke/lib/intermission.py` |
| Charts | Most requested songs, tonight and all time, and every Billboard Hot 100 number one. | `pikaraoke/lib/request_stats.py`, `number_ones.py` |
| Themes | Six looks for the guest and host pages, picked in Settings, Look. | `pikaraoke/lib/ui_themes.py`, `static/kn-themes.css` |
| Header logo | Optional; the host uploads one in Settings, Look. PNG, JPEG or WebP, checked by content. | `pikaraoke/lib/branding.py`, `routes/branding.py` |
| Sound | Output and device volume in Settings; the volume survives a power cut, saved 30 seconds after a change. | `deploy/files/kidnyce-karaoke-alsa-watch*` |
| Night plan | Time left tonight, songs that may not fit, a countdown on the singer screen for a song's last 20 seconds. | `pikaraoke/lib/night_plan.py`, `static/js/end-countdown.js` |
| Desktop or karaoke boot | Boot straight to the screens, or keep the desktop with icons. | `deploy/install-pi.sh --boot` |
| Guests on cellular (preview) | A Cloudflare Tunnel for guests only; host login never answers on it. | `deploy/files/kidnyce-karaoke-tunnel` |
| Security | Host only by default for every route, rate limits on joining and YouTube work, a warning when the app looks internet exposed. | `pikaraoke/lib/auth.py`, `security_checks.py`, `deploy/files/nginx-*.conf` |

## Releases

Bundles are numbered by branch: `main` is what runs at gigs, `preview` is where new
work is tried first. The app version shows it, for example `1.23.0+preview.0.11`, and
`sudo kidnyce-karaoke status` prints it as `preview v0.11`.

### preview v0.13

- Sound levels are saved 30 seconds after they change, driven by the sound
  card's change events, instead of a check every 2 minutes. Nothing runs while
  the levels stay put, and a clean shutdown inside the 30 seconds still saves.

### preview v0.12

- Upstream's latest fixes and its new icon set (see main v0.9).
- A first install asks for everything instead of assuming `kidnyce.com`: the
  Cloudflare token (or Enter for local network only), the domain picked from the
  ones the token can edit, the site name (`karaoke` by default), and how guests
  reach it.
- Guests through the tunnel at the main name (`--guests tunnel`): one address on
  the venue WiFi and on cellular. The host logs in at an optional second name
  (`--host-name`, such as `host.karaoke.example.com`, trusted certificate, same
  token) or on the Pi itself.
- Moving an existing install onto the tunnel replaces the Pi's own LAN address
  record and deletes the old test tunnel.
- The Karaoke Host icon opens the host name, or the app on the Pi itself when
  there is none (it used to open the domain, which fails through the tunnel or
  with no domain).

### preview v0.11

- The white logo plate above the navigation is gone. Pages start at the navigation
  bar unless the host uploads a header logo in Settings, Look (optional, any theme).
- Local network only mode: `--local-only`, or press Enter at the token prompt. No
  Cloudflare token, no domain, no certificate needed; phones use
  `http://<the Pi's address>`, which follows the Pi to a new network.
- `--url` accepts `{ip}` for the machine's current address.
- The screens' small print host address shows `http://` when that is what works.

### preview v0.10

- Six themes for the guest and host pages, previewed and applied in Settings, Look.
  Open pages switch at once.

### preview v0.9

- The device volume survives a power cut. The night end time has an on and off
  switch. The device volume box fits on a phone.

### preview v0.8

- Host login at the Pi's own address on any network, for venues whose router hides
  the karaoke name.

### preview v0.5 to v0.7

- Guests on cellular through a Cloudflare Tunnel (step by step setup in the guide),
  the desktop when there is no network at boot, a decade of number one hits
  downloaded ahead of a gig, time left tonight, and the singer countdown with a
  choice of where it sits.

### main v0.9

- Upstream PiKaraoke merged in (15 commits): smoother player controls between songs,
  no splash timestamp flashing, subtitles not rebuilt on every queue change, the
  waiting song skipped when autoplay is off, a toast on key change, punctuation
  folded in the library filter, and Phosphor icons in place of Fontello. Guest names
  stay escaped on the home page.

### main v0.8

- Ctrl+W or Alt+F4 closes the karaoke screens instead of reopening them.

### main v0.7

- First numbered release: installer, two screens, join codes, break music, charts,
  desktop mode, sound settings, and downloads that never freeze the song playing.

## Upstream updates

Upstream is merged in, never rebased over, so this fork's history stays intact. The
steps, for anyone maintaining a fork of this fork:

```sh
git remote add upstream https://github.com/vicwomg/pikaraoke.git
git fetch upstream
git switch main
git merge upstream/master
uv run pytest tests deploy/tests
```

Do not use "Discard commits" on GitHub's Sync fork button: it throws away this fork's
work. Where this fork and upstream touch the same lines, the fork keeps its security
behaviour (escaping text from guests' phones, host only routes) and takes upstream's
fixes and look around it. Many fork additions in shared files carry a `Kid Nyce`
comment, which makes them easy to find in a merge.
