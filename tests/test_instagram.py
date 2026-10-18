from pathlib import Path

import pytest

from philoreels.instagram import InstagramClient, InstagramError


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, statuses=("IN_PROGRESS", "FINISHED")):
        self.calls = []
        self.statuses = list(statuses)

    def post(self, url, data=None, headers=None, params=None, timeout=None):
        self.calls.append(("POST", url, data if isinstance(data, dict) else "<file>", headers))
        if url.endswith("/media"):
            return FakeResp(200, {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C1"})
        if "rupload" in url:
            return FakeResp(200, {"success": True, "message": "Upload successful."})
        if url.endswith("/media_publish"):
            return FakeResp(200, {"id": "M1"})
        raise AssertionError(url)

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params, None))
        if url.endswith("/C1"):
            return FakeResp(200, {"status_code": self.statuses.pop(0)})
        if url.endswith("/M1"):
            return FakeResp(200, {"permalink": "https://www.instagram.com/reel/abc/"})
        raise AssertionError(url)


def test_post_reel_resumable_flow(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 1234)
    http = FakeSession()
    client = InstagramClient("TOKEN", "IG1", session=http, sleep=lambda s: None)
    res = client.post_reel(video, "caption", thumb_offset_ms=2000)
    assert (res.container_id, res.media_id) == ("C1", "M1")
    assert res.permalink.endswith("/reel/abc/")
    create = http.calls[0]
    assert create[1] == "https://graph.facebook.com/v25.0/IG1/media"
    assert create[2]["media_type"] == "REELS" and create[2]["upload_type"] == "resumable"
    upload = http.calls[1]
    assert upload[3]["Authorization"] == "OAuth TOKEN" and upload[3]["file_size"] == "1234"
    assert http.calls[-2][1].endswith("/IG1/media_publish")


def test_processing_error_raises(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    client = InstagramClient("T", "IG1", session=FakeSession(["ERROR"]), sleep=lambda s: None)
    with pytest.raises(InstagramError):
        client.post_reel(video, "c")


def test_api_error_message_is_readable():
    client = InstagramClient("T", "IG1")
    with pytest.raises(InstagramError, match="Invalid OAuth"):
        client._check(FakeResp(400, {"error": {"message": "Invalid OAuth access token", "code": 190}}))
