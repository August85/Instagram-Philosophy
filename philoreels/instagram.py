"""Instagram Graph API client for publishing reels.

Only the official Content Publishing API is used:
https://developers.facebook.com/docs/instagram-platform/content-publishing

Flow for one reel:
  1. POST /{ig-user-id}/media            media_type=REELS -> container id
  2. upload the MP4 (resumable upload from this machine, or Instagram fetches video_url)
  3. GET  /{container-id}?fields=status_code until FINISHED
  4. POST /{ig-user-id}/media_publish    creation_id=container id -> media id
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests


class InstagramError(RuntimeError):
    def __init__(self, message: str, payload: Any = None, retryable: bool = False):
        super().__init__(message)
        self.payload = payload
        self.retryable = retryable


@dataclass
class PublishResult:
    container_id: str
    media_id: str
    permalink: str | None = None


class InstagramClient:
    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        api_version: str = "v25.0",
        graph_host: str = "https://graph.facebook.com",
        upload_host: str = "https://rupload.facebook.com",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token = access_token
        self.ig_user_id = ig_user_id
        self.version = api_version
        self.graph = f"{graph_host}/{api_version}"
        self.upload_host = upload_host
        self.http = session or requests.Session()
        self.sleep = sleep

    # -- low level -----------------------------------------------------------
    def _check(self, resp: requests.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:500]}
        if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}"
            retryable = resp.status_code >= 500 or err.get("is_transient", False) or err.get("code") in (1, 2, 4, 17, 341)
            raise InstagramError(f"Instagram API error: {msg}", data, retryable=retryable)
        return data

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        params["access_token"] = self.token
        return self._check(self.http.get(f"{self.graph}/{path}", params=params, timeout=60))

    def _post(self, path: str, **params: Any) -> dict[str, Any]:
        params["access_token"] = self.token
        return self._check(self.http.post(f"{self.graph}/{path}", data=params, timeout=60))

    # -- account -------------------------------------------------------------
    def account(self) -> dict[str, Any]:
        return self._get(self.ig_user_id, fields="username,name,followers_count,media_count")

    def publishing_limit(self) -> dict[str, Any]:
        data = self._get(f"{self.ig_user_id}/content_publishing_limit", fields="quota_usage,config")
        rows = data.get("data") or [{}]
        return rows[0]

    # -- publishing ----------------------------------------------------------
    def create_reel_container(
        self,
        caption: str,
        video_url: str | None = None,
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if thumb_offset_ms is not None:
            params["thumb_offset"] = int(thumb_offset_ms)
        if video_url:
            params["video_url"] = video_url
        else:
            params["upload_type"] = "resumable"
        return self._post(f"{self.ig_user_id}/media", **params)

    def upload_file(self, container: dict[str, Any], path: Path) -> dict[str, Any]:
        uri = container.get("uri") or f"{self.upload_host}/ig-api-upload/{self.version}/{container['id']}"
        size = path.stat().st_size
        with path.open("rb") as fh:
            resp = self.http.post(
                uri,
                headers={"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size)},
                data=fh,
                timeout=600,
            )
        data = self._check(resp)
        if data.get("success") is False:
            raise InstagramError(f"Upload failed: {data}", data, retryable=True)
        return data

    def container_status(self, container_id: str) -> dict[str, Any]:
        return self._get(container_id, fields="status_code,status")

    def wait_until_ready(self, container_id: str, timeout: float = 900, interval: float = 20) -> None:
        waited = 0.0
        while True:
            st = self.container_status(container_id)
            code = st.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram could not process the video ({code}): {st.get('status')}", st)
            if waited >= timeout:
                raise InstagramError(f"Video still processing after {int(timeout)} s", st, retryable=True)
            self.sleep(interval)
            waited += interval

    def publish(self, container_id: str) -> str:
        data = self._post(f"{self.ig_user_id}/media_publish", creation_id=container_id)
        return data["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._get(media_id, fields="permalink").get("permalink")
        except InstagramError:
            return None

    def post_reel(
        self,
        video: Path,
        caption: str,
        video_url: str | None = None,
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
    ) -> PublishResult:
        container = self.create_reel_container(caption, video_url, share_to_feed, thumb_offset_ms)
        cid = container["id"]
        if not video_url:
            self.upload_file(container, video)
        self.wait_until_ready(cid)
        media_id = self.publish(cid)
        return PublishResult(container_id=cid, media_id=media_id, permalink=self.permalink(media_id))


# -- tokens -----------------------------------------------------------------
def exchange_long_lived(app_id: str, app_secret: str, token: str, api_version: str = "v25.0",
                        session: requests.Session | None = None) -> dict[str, Any]:
    """Swap a short-lived (or expiring long-lived) user token for a fresh ~60-day token."""
    http = session or requests.Session()
    resp = http.get(
        f"https://graph.facebook.com/{api_version}/oauth/access_token",
        params={"grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret, "fb_exchange_token": token},
        timeout=60,
    )
    return InstagramClient(token, "me")._check(resp)


def find_instagram_accounts(token: str, api_version: str = "v25.0",
                            session: requests.Session | None = None) -> list[dict[str, Any]]:
    """List the Facebook Pages this token can see and the Instagram account linked to each."""
    client = InstagramClient(token, "me", api_version=api_version, session=session)
    data = client._get("me/accounts", fields="name,instagram_business_account{id,username}")
    out = []
    for page in data.get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            out.append({"page": page.get("name"), "ig_user_id": ig["id"], "username": ig.get("username")})
    return out
