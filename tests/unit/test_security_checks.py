"""The startup warning for an app that looks reachable from the internet."""

import pytest

from pikaraoke.lib.security_checks import exposure_warning


@pytest.mark.parametrize(
    "bind, url",
    [
        ("127.0.0.1", "http://8.8.8.8:5555"),  # behind a proxy on loopback
        ("::1", "https://karaoke.example.com"),
        ("0.0.0.0", "http://192.168.1.50:5555"),  # LAN only
        ("0.0.0.0", "http://10.0.0.5:5555"),
        ("192.168.1.50", "http://192.168.1.50:5555"),
        ("0.0.0.0", "http://karaoke.local:5555"),  # a name is not checked
        ("0.0.0.0", "https://karaoke.djkidnyce.com"),
    ],
)
def test_no_warning_on_the_lan_or_behind_a_proxy(bind, url):
    assert exposure_warning(bind, url) is None


def test_a_public_bind_address_warns():
    warning = exposure_warning("8.8.8.8", "http://8.8.8.8:5555")
    assert warning and "8.8.8.8" in warning and "plain HTTP" in warning


@pytest.mark.parametrize("url", ["http://8.8.8.8:5555", "http://[2001:4860:4860::8888]:5555"])
def test_a_public_url_on_all_interfaces_warns(url):
    warning = exposure_warning("0.0.0.0", url)
    assert warning and url in warning
