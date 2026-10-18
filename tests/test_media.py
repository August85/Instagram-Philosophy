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


def _jpeg_for(url: str) -> bytes:
    """A different random picture per URL, so the duplicate check keeps them apart."""
    import io
    import zlib
    rng = np.random.default_rng(zlib.crc32(url.encode()))
    img = Image.fromarray(rng.integers(0, 255, (10, 14, 3), dtype=np.uint8)).resize((1400, 1000))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class FakeSession:
    headers: dict = {}

    def __init__(self, jpeg: bytes | None = None):
        self.headers = {}
        self.jpeg = jpeg

    def get(self, url, params=None, timeout=None):
        if "api.openverse.org" in url:
            return FakeResponse({"results": [
                {"id": "ov1", "title": "Village pond", "creator": "A", "license": "cc0", "url": "https://x/ov1.jpg",
                 "foreign_landing_url": "https://flickr/ov1", "width": 2000, "height": 1300},
                {"id": "ov2", "title": "Farmer portrait", "creator": "B", "license": "pdm", "url": "https://x/face.jpg",
                 "foreign_landing_url": "https://flickr/ov2", "width": 2000, "height": 1300},
            ]})
        if "pixabay.com/api" in url:
            assert params["key"] == "test-key"
            return FakeResponse({"hits": [{"id": 5, "tags": "village, field", "user": "C",
                                           "pageURL": "https://pixabay/5", "largeImageURL": "https://x/px5.jpg"}]})
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
        return FakeResponse(content=self.jpeg or _jpeg_for(url))


def test_fetch_keeps_only_public_domain(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    f = images.Fetcher(FakeSession())
    saved = images.fetch(tmp_path, limit=10, fetcher=f, log=lambda *_: None, sources=["commons", "met"],
                         reject_faces=False)
    assert len(saved) == 2
    credits = (tmp_path / "CREDITS.md").read_text()
    assert "Krishna and Arjuna" in credits and "Modern" not in credits


def test_fetch_drops_pictures_with_faces(tmp_path, monkeypatch):
    import io
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    f = images.Fetcher(FakeSession())
    by_url = {}
    real_download = f.download

    def download(pic, folder):
        path = real_download(pic, folder)
        by_url[str(path)] = pic.image_url
        return path
    monkeypatch.setattr(f, "download", download)

    def faces(paths):
        return {str(p): (2 if by_url[str(p)].endswith("face.jpg") else 0) for p in paths}
    saved = images.fetch(tmp_path, limit=10, fetcher=f, log=lambda *_: None,
                         sources=["openverse", "pixabay"], queries=["Indian village"], face_counter=faces)
    names = sorted(p.name for p in saved)
    assert len(names) == 2 and all(p.exists() for p in saved)
    assert not any("farmer" in n for n in names) and not list(tmp_path.glob("openverse-farmer*"))
    credits = (tmp_path / "CREDITS.md").read_text()
    assert "Pixabay Content License" in credits and "CC0" in credits and "Farmer" not in credits
    # A second run keeps the credit rows of pictures already in the folder.
    images.fetch(tmp_path, limit=0, fetcher=f, log=lambda *_: None, sources=[], face_counter=faces)
    assert (tmp_path / "CREDITS.md").read_text().count("| openverse-") == 1


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


def test_no_face_detector_means_review_by_eye(tmp_path, monkeypatch):
    from philoreels import faces
    monkeypatch.setattr(faces, "_vision", lambda paths: None)
    monkeypatch.setattr(faces, "_opencv", lambda paths: None)
    assert faces.count_faces([tmp_path / "a.jpg"]) is None
    assert faces.count_faces([]) == {}


class OpenverseSession(FakeSession):
    """Openverse as it really answers: Commons titles as HTML, one result per query."""

    def get(self, url, params=None, timeout=None):
        if "api.openverse.org" in url:
            q = params["q"]
            return FakeResponse({"results": [
                {"id": f"id-{q}", "title": "<div class='fn'> Paddy field &amp; hut | Kerala</div>", "creator": "<b>D</b>",
                 "license": "cc0", "url": f"https://x/{q}.jpg", "foreign_landing_url": "https://c/1",
                 "width": 2000, "height": 1300},
                {"id": f"id2-{q}", "title": f"Second {q}", "creator": "E", "license": "pdm",
                 "url": f"https://x/{q}-2.jpg", "foreign_landing_url": "https://c/2", "width": 2000, "height": 1300},
            ]})
        return super().get(url, params, timeout)


def test_openverse_titles_are_plain_text(tmp_path):
    f = images.Fetcher(OpenverseSession())
    pics = f.openverse(["paddy"])
    assert pics[0].title == "Paddy field & hut | Kerala" and pics[0].artist == "D"
    assert pics[0].filename.startswith("openverse-paddy-field-hut-kerala-")
    images.write_credits(tmp_path, [(pics[0], tmp_path / pics[0].filename)])
    row = (tmp_path / "CREDITS.md").read_text().splitlines()[-1]
    assert row.count("|") == 6 and "<div" not in row


def test_fetch_takes_turns_between_queries(tmp_path):
    f = images.Fetcher(OpenverseSession())
    saved = images.fetch(tmp_path, limit=3, fetcher=f, log=lambda *_: None, sources=["openverse"],
                         queries=["a", "b", "c"], reject_faces=False)
    # One picture from each query, not both from the first two.
    assert len(saved) == 3
    assert len({p.name.split("-")[-1] for p in saved}) == 3
    assert not any(p.name.startswith("openverse-second") for p in saved)


def test_fetch_skips_the_same_picture_from_two_sources(tmp_path):
    session = FakeSession(_jpeg_for("same"))
    f = images.Fetcher(session)
    saved = images.fetch(tmp_path, limit=10, fetcher=f, log=lambda *_: None, sources=["openverse", "commons"],
                         queries=["village"], reject_faces=False)
    assert len(saved) == 1
    # A later run doesn't add it again under another name either.
    f2 = images.Fetcher(FakeSession(_jpeg_for("same")))
    assert images.fetch(tmp_path, limit=10, fetcher=f2, log=lambda *_: None, sources=["commons"],
                        queries=["village"], reject_faces=False) == []
