"""Browser level checks for a running install, driven by Playwright Chromium.

Run by sandbox-e2e.sh after the installer. Plays the night through: the two
screens open on the Pi, a phone scans in, songs get queued, a break comes
round. Also opens the one screen setup and the host's Screens settings.
Writes screenshots and exits non zero if anything is off.

Playwright's Chromium cannot decode H.264, so the karaoke video itself does
not play here (the Pi's Chromium does). Everything around it is checked.

    python3 browser_checks.py DOMAIN PORT JOIN_CODE ADMIN_PASSWORD SONG_A SONG_B OUT_DIR
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright  # pylint: disable=import-error

DOMAIN, PORT, CODE, ADMIN_PASSWORD, SONG_A, SONG_B, OUT = sys.argv[1:8]
OUT_DIR = Path(OUT)
OUT_DIR.mkdir(parents=True, exist_ok=True)
SITE = f"https://{DOMAIN}"
LOCAL = f"http://127.0.0.1:{PORT}"

results: list[tuple[str, bool, str]] = []
console_problems: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail and not ok else ''}")


def watch_console(page: Page, label: str) -> None:
    def on_console(msg):
        text = msg.text
        if "Content Security Policy" in text or "Refused to" in text:
            console_problems.append(f"{label} {page.url}: {text}")

    def on_error(err):
        # This Chromium has no H.264, so the player's retry of a video it cannot
        # decode throws. The Pi's Chromium decodes it; nothing to fix here.
        if "no supported sources" in str(err):
            return
        # Upstream PiKaraoke: the empty Add New page looks up the preview modal,
        # which only renders with results. Harmless, and left as upstream wrote it.
        if page.url.split("?")[0].endswith("/search") and "querySelector" in str(err):
            return
        console_problems.append(f"{label} {page.url}: JS error: {err}")

    page.on("console", on_console)
    page.on("pageerror", on_error)


def wait_for(predicate, timeout: float = 30.0, step: float = 0.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return True
        except Exception:  # pylint: disable=broad-except
            pass
        time.sleep(step)
    return False


with sync_playwright() as p:
    browser = p.chromium.launch(
        args=[
            "--no-proxy-server",
            f"--host-resolver-rules=MAP {DOMAIN} 127.0.0.1",
            "--autoplay-policy=no-user-gesture-required",
        ]
    )

    # The two screens, on the Pi itself over loopback, as the kiosk opens them.
    screens = browser.new_context(viewport={"width": 1920, "height": 1080})
    singer = screens.new_page()
    watch_console(singer, "singer")
    singer.goto(f"{LOCAL}/splash?singer=1")
    # Without H.264 here the autoplay probe fails and the splash asks for one
    # click, as it would on any browser that blocks autoplay. The kiosk's
    # Chromium on the Pi has H.264 and the autoplay flag, so it never asks.
    if wait_for(lambda: singer.is_visible("#permissions-button"), 5):
        singer.click("#permissions-button")
    crowd = screens.new_page()
    watch_console(crowd, "crowd")
    crowd.goto(f"{LOCAL}/crowd")

    expected_code = f"{CODE[:3]} {CODE[3:]}"
    check(
        "crowd screen shows the join code",
        wait_for(lambda: crowd.inner_text("#join-code") == expected_code, 15),
        crowd.inner_text("#join-code"),
    )
    check(
        "crowd QR image loads",
        wait_for(
            lambda: crowd.evaluate("document.getElementById('crowd-qr').naturalWidth") > 0, 10
        ),
    )
    check("crowd URL line shows the host", DOMAIN in crowd.inner_text("#join-url"))
    check(
        "singer screen hides the QR code",
        singer.evaluate(
            "[...document.querySelectorAll('.splash-qr-image')].every(e => getComputedStyle(e).display === 'none')"
        ),
    )
    crowd.screenshot(path=str(OUT_DIR / "crowd-idle.png"))

    # A guest's phone.
    phone_ctx = browser.new_context(**p.devices["iPhone 13"], ignore_https_errors=True)
    phone = phone_ctx.new_page()
    watch_console(phone, "phone")
    phone.goto(f"{SITE}/")
    check("phone without the code lands on /join", phone.url.endswith("/join"), phone.url)
    phone.screenshot(path=str(OUT_DIR / "phone-join.png"))
    phone.goto(f"{SITE}/join/{CODE}")
    check("scanning the QR address joins", phone.url.rstrip("/") == SITE, phone.url)
    check(
        "phone socket upgrades to a websocket through Nginx",
        wait_for(
            lambda: phone.evaluate(
                "window.socket && window.socket.io.engine && window.socket.io.engine.transport.name"
            )
            == "websocket",
            15,
        ),
    )
    phone.screenshot(path=str(OUT_DIR / "phone-home.png"))

    # The host turns breaks on: one after every singer, 30 seconds long.
    admin_ctx = browser.new_context(ignore_https_errors=True)
    login = admin_ctx.request.post(f"{SITE}/api/auth", data={"admin_password": ADMIN_PASSWORD})
    check("host logs in", login.ok and login.json().get("authenticated") is True)
    for pref, val in {
        "enable_intermission": "True",
        "intermission_minutes": "0.5",
        "intermission_every": "1",
        "disable_score": "True",
    }.items():
        r = admin_ctx.request.post(
            f"{SITE}/api/change_preferences", form={"pref": pref, "val": val}
        )
        check(f"host sets {pref}", r.ok and r.json()[0] is True)
    preview = admin_ctx.request.get(f"{SITE}/api/intermission/preview?minutes=0.5").json()
    check("break planner previews a plan", preview["track_count"] >= 1, json.dumps(preview))

    # Two guests queue songs.
    for song, singer_name in ((SONG_A, "Ana"), (SONG_B, "Bo")):
        r = phone.request.post(
            f"{SITE}/api/enqueue", form={"song_to_add": song, "song_added_by": singer_name}
        )
        check(f"{singer_name} queues a song", r.ok and r.json()["success"][0] is True, r.text())

    check(
        "crowd shows Ana singing",
        wait_for(
            lambda: crowd.is_visible("#now-card") and crowd.inner_text("#now-singer") == "Ana", 45
        ),
    )
    check(
        "crowd lists Bo next",
        wait_for(lambda: "Bo" in crowd.inner_text("#queue-list"), 10),
    )
    check(
        "crowd copy of the video is muted",
        crowd.evaluate("document.getElementById('crowd-video').muted") is True,
    )
    check(
        "crowd video follows the same stream",
        wait_for(
            lambda: ".m3u8"
            in (
                crowd.evaluate(
                    "window.performance.getEntriesByType('resource').map(e => e.name).join(' ')"
                )
                or ""
            ),
            20,
        ),
    )
    # The test songs are 12 seconds long, inside the 20 second countdown, so the
    # countdown is either hidden (not started) or shows the seconds left.
    countdown = singer.evaluate(
        "(() => { const e = document.getElementById('kn-end-countdown');"
        " return e ? {hidden: e.hidden, text: e.textContent} : null; })()"
    )
    check(
        "singer screen has the end of song countdown",
        countdown is not None
        and (
            countdown["hidden"]
            or (countdown["text"].isdigit() and 1 <= int(countdown["text"]) <= 20)
        ),
        str(countdown),
    )
    admin_ctx.request.post(
        f"{SITE}/api/change_preferences",
        form={"pref": "end_countdown_position", "val": "bottom-right"},
    )
    check(
        "the host moves the countdown and the singer screen follows at once",
        wait_for(
            lambda: singer.evaluate(
                "document.getElementById('kn-end-countdown').classList.contains('pos-bottom-right')"
            ),
            10,
        ),
    )
    admin_ctx.request.post(
        f"{SITE}/api/change_preferences",
        form={"pref": "end_countdown_position", "val": "top-center"},
    )
    check(
        "the room screen has no countdown",
        crowd.evaluate("document.getElementById('kn-end-countdown') === null"),
    )
    crowd.screenshot(path=str(OUT_DIR / "crowd-singing.png"))
    singer.screenshot(path=str(OUT_DIR / "singer-singing.png"))

    # The one screen setup, as the kiosk opens it with a single TV. It joins as
    # a follower, since the singer screen above is already the player.
    one = screens.new_page()
    watch_console(one, "one screen")
    one.goto(f"{LOCAL}/splash?all=1")
    check(
        "one screen setup shows the join code",
        wait_for(lambda: one.inner_text("#splash-join-code") == expected_code, 15),
        one.inner_text("#splash-join-code"),
    )
    check(
        "one screen setup lists who is next",
        wait_for(lambda: "Bo" in one.inner_text("#all-queue-list"), 10),
    )
    check(
        "one screen setup shows the QR code",
        one.evaluate(
            "[...document.querySelectorAll('#qr-code .splash-qr-image')].some(e => getComputedStyle(e).display !== 'none' && e.naturalWidth > 0)"
        ),
    )
    one.screenshot(path=str(OUT_DIR / "one-screen.png"))
    one.close()

    # The song cannot decode here, so the singer screen ends it after its
    # start timeout, and the break is due before Bo.
    check(
        "break starts between singers (crowd)",
        wait_for(lambda: crowd.is_visible("#break-panel"), 60),
    )
    check(
        "break starts between singers (singer screen)",
        wait_for(lambda: singer.is_visible("#intermission-overlay"), 10),
    )
    check("break names the next singer", "Bo" in crowd.inner_text("#break-next-singer"))
    check(
        "singer screen plays the break music",
        wait_for(
            lambda: singer.evaluate("!document.getElementById('intermission-audio').paused"), 15
        ),
    )
    check(
        "crowd screen stays silent during the break",
        crowd.evaluate("document.querySelectorAll('audio').length") == 0,
    )
    time.sleep(2)
    crowd.screenshot(path=str(OUT_DIR / "crowd-break.png"))
    singer.screenshot(path=str(OUT_DIR / "singer-break.png"))

    r = admin_ctx.request.post(f"{SITE}/api/intermission/skip")
    check("host ends the break", r.ok)
    check(
        "Bo comes up after the break",
        wait_for(lambda: crowd.inner_text("#now-singer") == "Bo", 45),
    )

    phone.goto(f"{SITE}/charts")
    check(
        "charts list the requests",
        wait_for(lambda: phone.locator("#requested-list .bar-row").count() == 2, 15),
    )
    check(
        "charts list number one hits",
        wait_for(lambda: phone.locator("#number-ones-list .one-row").count() >= 50, 15),
    )
    phone.screenshot(path=str(OUT_DIR / "phone-charts.png"), full_page=False)

    for path in ("/queue", "/search", "/browse", "/rankings", "/info", "/settings", "/charts"):
        phone.goto(f"{SITE}{path}")
        phone.wait_for_load_state("networkidle")
    admin_page = admin_ctx.new_page()
    watch_console(admin_page, "host")
    admin_page.goto(f"{SITE}/")
    check(
        "host panel shows the join code", admin_page.inner_text("#host-join-code").strip() == CODE
    )
    admin_page.goto(f"{SITE}/settings")
    admin_page.wait_for_load_state("networkidle")
    check(
        "Settings, Screens names the board",
        wait_for(lambda: "GB RAM" in admin_page.inner_text("#screens-board"), 10),
        admin_page.inner_text("#screens-board"),
    )
    admin_page.click("h3:text-is('Screens')")
    admin_page.select_option("#pref-screen-mode", "single")
    check(
        "host picks one screen in Settings",
        wait_for(
            lambda: admin_ctx.request.get(f"{SITE}/api/display/hardware").json()["settings"][
                "screen_mode"
            ]
            == "single",
            10,
        ),
    )
    check(
        "the swap option hides with one screen",
        wait_for(lambda: not admin_page.is_visible("#screens-swap-field"), 10),
    )
    admin_page.screenshot(path=str(OUT_DIR / "host-screens.png"), full_page=True)
    admin_page.select_option("#pref-screen-mode", "auto")
    check(
        "host goes back to Auto",
        wait_for(
            lambda: admin_ctx.request.get(f"{SITE}/api/display/hardware").json()["settings"][
                "screen_mode"
            ]
            == "auto",
            10,
        ),
    )

    def audio() -> dict:
        return admin_ctx.request.get(f"{SITE}/api/display/hardware").json()["audio"]

    admin_page.click("h3:text-is('Sound')")
    admin_page.select_option("#pref-audio-output", "headphones")
    check(
        "host picks the headphone jack in Settings, Sound",
        wait_for(lambda: audio()["output"] == "headphones", 10),
    )
    admin_page.fill("#pref-audio-level", "70")
    admin_page.dispatch_event("#pref-audio-level", "change")
    check("host sets the device volume", wait_for(lambda: audio()["level"] == 70, 10))
    admin_page.fill("#pref-audio-level", "")
    admin_page.dispatch_event("#pref-audio-level", "change")
    admin_page.select_option("#pref-audio-output", "")
    check(
        "host goes back to the install default sound",
        wait_for(lambda: audio() == {"output": "", "level": -1}, 10),
    )
    admin_page.screenshot(path=str(OUT_DIR / "host-settings.png"), full_page=True)

    check(
        "no CSP violations or JS errors on any page",
        not console_problems,
        "; ".join(console_problems[:5]),
    )
    browser.close()

failed = [name for name, ok, _ in results if not ok]
print(f"Browser checks: {len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
