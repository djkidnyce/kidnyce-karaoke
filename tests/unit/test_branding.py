"""The optional header logo: what is accepted, where it lives, who may change it."""

import io

import pytest

from pikaraoke.lib import branding
from pikaraoke.routes.branding import branding_bp
from tests.conftest import make_route_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "get_data_directory", lambda: str(tmp_path))
    return tmp_path


class TestImageType:
    @pytest.mark.parametrize("data,kind", [(PNG, "png"), (JPG, "jpg"), (WEBP, "webp")])
    def test_known_types(self, data, kind):
        assert branding.image_type(data) == kind

    @pytest.mark.parametrize("data", [SVG, b"GIF89a", b"", b"RIFF1234AVI "])
    def test_everything_else_is_refused(self, data):
        assert branding.image_type(data) is None


class TestSave:
    def test_nothing_saved_means_no_logo(self):
        assert branding.current() is None
        assert branding.version() is None

    def test_saves_under_the_data_directory(self, data_dir):
        path = branding.save(PNG)
        assert path == str(data_dir / "branding" / "header-logo.png")
        assert branding.current() == (path, "image/png")
        assert isinstance(branding.version(), int)

    def test_a_new_type_replaces_the_old_file(self, data_dir):
        branding.save(PNG)
        branding.save(JPG)
        assert sorted(p.name for p in (data_dir / "branding").iterdir()) == ["header-logo.jpg"]

    @pytest.mark.parametrize(
        "data,reason",
        [(b"", "empty"), (SVG, "not_an_image"), (PNG + b"\x00" * branding.MAX_BYTES, "too_big")],
    )
    def test_refusals_keep_the_old_logo(self, data, reason):
        branding.save(PNG)
        with pytest.raises(ValueError, match=reason):
            branding.save(data)
        assert branding.current()[1] == "image/png"

    def test_remove_is_safe_with_nothing_there(self):
        branding.remove()
        branding.save(WEBP)
        branding.remove()
        assert branding.current() is None


def _client(admin=True):
    return make_route_app(branding_bp, [], admin=admin).test_client()


class TestRoutes:
    def test_no_logo_is_a_404(self):
        assert _client().get("/branding/logo").status_code == 404

    def test_upload_then_serve(self):
        c = _client()
        r = c.post("/api/branding/logo", data={"logo": (io.BytesIO(PNG), "x.png")})
        assert r.status_code == 200 and r.json["ok"] and r.json["version"]
        got = c.get("/branding/logo")
        assert got.status_code == 200
        assert got.mimetype == "image/png"
        assert got.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in got.headers["Content-Security-Policy"]
        assert got.data == PNG

    def test_file_name_does_not_decide_the_type(self):
        r = _client().post("/api/branding/logo", data={"logo": (io.BytesIO(SVG), "logo.png")})
        assert r.status_code == 400 and not r.json["ok"]
        assert branding.current() is None

    def test_missing_file(self):
        r = _client().post("/api/branding/logo", data={})
        assert r.status_code == 400

    def test_delete(self):
        branding.save(PNG)
        r = _client().delete("/api/branding/logo")
        assert r.status_code == 200 and r.json["ok"]
        assert branding.current() is None

    def test_guests_can_see_it_but_not_change_it(self):
        branding.save(PNG)
        guest = _client(admin=False)
        assert guest.get("/branding/logo").status_code == 200
        r = guest.post("/api/branding/logo", data={"logo": (io.BytesIO(JPG), "x.jpg")})
        assert r.status_code in (302, 401, 403)
        assert guest.delete("/api/branding/logo").status_code in (302, 401, 403)
        assert branding.current()[1] == "image/png"
