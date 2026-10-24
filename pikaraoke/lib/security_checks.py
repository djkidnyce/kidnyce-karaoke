"""Startup check: warn when the app looks reachable from the internet."""

import ipaddress
from urllib.parse import urlsplit


def _ip(text: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text or "")
    except ValueError:
        return None


def exposure_warning(bind_address: str, url: str) -> str | None:
    """A warning when the app is likely reachable from the internet, else None.

    The app serves plain HTTP, so on a public address the admin password and
    join codes cross the internet in clear text. Behind a reverse proxy on
    loopback, as the Pi installer sets it up, there is nothing to warn about.
    """
    bind = _ip(bind_address)
    if bind is not None and bind.is_loopback:
        return None
    if bind is not None and bind.is_global:
        return (
            f"SECURITY WARNING: listening on public address {bind_address}. "
            "Anyone on the internet can reach this server over plain HTTP. "
            "Bind to 127.0.0.1 behind a reverse proxy with TLS, or to a LAN address."
        )
    host = _ip(urlsplit(url).hostname)
    if host is not None and host.is_global:
        return (
            f"SECURITY WARNING: the URL guests are given ({url}) is a public address. "
            "If a router forwards this port, anyone on the internet can reach this "
            "server over plain HTTP. Keep it on the LAN or put a reverse proxy with TLS in front."
        )
    return None
