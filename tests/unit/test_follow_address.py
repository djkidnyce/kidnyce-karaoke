"""--url http://{ip}: an install with no domain name follows the network it joins."""

from unittest.mock import MagicMock, patch

import pytest

from pikaraoke import karaoke as karaoke_module
from pikaraoke.karaoke import Karaoke


def _stub(url_override, ip="172.16.0.142"):
    k = MagicMock()
    k.is_raspberry_pi = False
    k.platform = "linux"
    k.url_override = url_override
    k.url_base_path = ""
    k.prefer_hostname = False
    k.port = 5555
    k.ip = ip
    k.url = None
    k._next_address_check = 0
    k.follows_address = lambda: Karaoke.follows_address(k)
    k._current_ip = lambda: karaoke_module.get_ip(k.platform)
    return k


class TestPlaceholder:
    def test_ip_goes_into_the_url(self):
        k = _stub("http://{ip}")
        with patch.object(karaoke_module, "get_ip", return_value="10.1.2.3"):
            assert Karaoke.get_url(k) == "http://10.1.2.3"

    def test_a_domain_url_is_left_alone(self):
        k = _stub("https://karaoke.kidnyce.com")
        with patch.object(karaoke_module, "get_ip", return_value="10.1.2.3"):
            assert Karaoke.get_url(k) == "https://karaoke.kidnyce.com"
        assert not Karaoke.follows_address(k)

    def test_no_url_keeps_upstream_behaviour(self):
        k = _stub(None)
        with patch.object(karaoke_module, "get_ip", return_value="10.1.2.3"):
            assert Karaoke.get_url(k) == "http://10.1.2.3:5555"
        assert not Karaoke.follows_address(k)


class TestFollow:
    def test_new_network_updates_url_qr_and_screens(self):
        k = _stub("http://{ip}")
        with patch.object(karaoke_module, "get_ip", return_value="192.168.1.50"):
            Karaoke._follow_address(k)
        assert k.ip == "192.168.1.50"
        assert k.url == "http://192.168.1.50"
        k.generate_qr_code.assert_called_once()
        k.events.emit.assert_called_once_with("address_changed")

    @pytest.mark.parametrize("ip", ["172.16.0.142", "", "127.0.0.1"])
    def test_same_address_or_no_network_changes_nothing(self, ip):
        k = _stub("http://{ip}")
        with patch.object(karaoke_module, "get_ip", return_value=ip):
            Karaoke._follow_address(k)
        assert k.ip == "172.16.0.142"
        k.generate_qr_code.assert_not_called()

    def test_checks_at_most_every_interval(self):
        k = _stub("http://{ip}")
        with patch.object(karaoke_module, "get_ip", return_value="192.168.1.50") as got:
            Karaoke._follow_address(k)
            Karaoke._follow_address(k)
        assert got.call_count == 1

    def test_domain_installs_never_look(self):
        k = _stub("https://karaoke.kidnyce.com")
        with patch.object(karaoke_module, "get_ip") as got:
            Karaoke._follow_address(k)
        got.assert_not_called()
