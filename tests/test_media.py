from pathlib import Path

import numpy as np
from PIL import Image

from philoreels import images, music


def test_public_domain_filter():
    assert images.is_public_domain("Public domain")
    assert images.is_public_domain("CC0")
    assert images.is_public_domain("PD-old")
    assert not images.is_public_domain("CC BY-SA 4.0")
    assert not images.is_public_domain("")


class FakeResponse:
    def __init__(self, data=None, content=b"", status=200):
        self._data, self.content, self.status_code = data, content, status

    def json(self):
        return self._data


class FakeSession:
    headers: dict = {}

    def __init__(self, jpeg: bytes):
        self.headers = {}
        self.jpeg = jpeg

    def get(self, url, params=None, timeout=None):
        if "commons" in url:
            pages = {
                "1": {"title": "File:Krishna.jpg", "imageinfo": [{"width": 1600, "height": 1200, "url": "https://x/k.jpg",
                      "descriptionurl": "https://c/k", "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]},
                "2": {"title": "File:Modern.jpg", "imageinfo": [{"width": 1600, "height": 1200, "url": "https://x/m.jpg",
                      "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}]},
            }
            return FakeResponse({"query": {"pages": pages}})
        if url.endswith("/search"):
            assert "/v1.1/" in url, "the Met retired v1/search on 2026-10-01"
            return FakeResponse({"objectIDs": [7, 8]})
        if url.endswith("/objects/7"):
            return FakeResponse({"isPublicDomain": True, "primaryImage": "https://x/met.jpg", "title": "Krishna and Arjuna",
                                 "classification": "Paintings", "objectURL": "https://met/7"})
        if url.endswith("/objects/8"):
            return FakeResponse({"isPublicDomain": False, "primaryImage": "https://x/no.jpg"})
        return FakeResponse(content=self.jpeg)


def test_fetch_keeps_only_public_domain(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    buf = io.BytesIO()
    Image.new("RGB", (1400, 1000), "orange").save(buf, "JPEG")
    f = images.Fetcher(FakeSession(buf.getvalue()))
    saved = images.fetch(tmp_path, limit=10, fetcher=f, log=lambda *_: None)
    assert len(saved) == 2
    credits = (tmp_path / "CREDITS.md").read_text()
    assert "Krishna and Arjuna" in credits and "Modern" not in credits


def test_fetcher_overrides_default_user_agent():
    # Wikimedia returns 403 to requests' default "python-requests/x" agent.
    import requests
    f = images.Fetcher(requests.Session())
    assert f.http.headers["User-Agent"] == images.USER_AGENT


def test_music_synth_is_clean_stereo():
    st = music.synth(music.SPECS[0], duration=8.0)
    assert st.shape == (8 * music.SR, 2)
    assert np.max(np.abs(st)) <= 0.81
    assert np.all(np.isfinite(st))
