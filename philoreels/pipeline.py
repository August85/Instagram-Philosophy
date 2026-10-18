"""Glue between recipes, rendering, the schedule and the Instagram client."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from typing import Any

from . import render as render_mod
from . import schedule
from .config import Config
from .content import Recipe, load_recipes
from .instagram import InstagramClient, InstagramError, exchange_long_lived
from .store import Store

log = logging.getLogger("philoreels")

MAX_TRIES_PER_REEL = 2
TOKEN_REFRESH_AFTER_DAYS = 45


def notify(message: str) -> None:
    """Show a macOS notification when possible; always log it."""
    log.warning(message)
    if shutil.which("osascript"):
        safe = message.replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "PhiloReels"'],
                       capture_output=True)


def queue(cfg: Config, store: Store, recipes: list[Recipe] | None = None) -> list[Recipe]:
    """Recipes still to post, in order, minus ones that already failed twice."""
    recipes = recipes if recipes is not None else load_recipes(cfg.root)
    done = store.posted_ids()
    return [r for r in recipes if r.id not in done and store.failures_for(r.id) < MAX_TRIES_PER_REEL]


def rendered_ahead(cfg: Config, pending: list[Recipe]) -> int:
    n = 0
    for r in pending:
        if render_mod.video_path(cfg, r.id).exists():
            n += 1
        else:
            break
    return n


def render_ahead(cfg: Config, store: Store, limit: int | None = None) -> list[str]:
    """Render the next unrendered reels so `render.render_ahead` are ready."""
    pending = queue(cfg, store)
    want = cfg.get("render.render_ahead", 12)
    done = []
    for r in pending[:want]:
        if limit is not None and len(done) >= limit:
            break
        if not render_mod.video_path(cfg, r.id).exists():
            render_mod.render(cfg, r)
            done.append(r.id)
    return done


def make_client(cfg: Config, store: Store) -> InstagramClient:
    tok = store.token
    token = os.environ.get("IG_ACCESS_TOKEN") or tok.get("access_token")
    ig_id = os.environ.get("IG_USER_ID") or tok.get("ig_user_id")
    if not token or not ig_id:
        raise InstagramError("No Instagram token yet. Run: philoreels auth --token <token from Graph API Explorer>")
    return InstagramClient(token, ig_id, api_version=cfg.get("instagram.api_version", "v25.0"))


def maybe_refresh_token(cfg: Config, store: Store, now: datetime) -> bool:
    tok = store.token
    obtained = tok.get("obtained_at")
    app_id, secret = os.environ.get("FB_APP_ID"), os.environ.get("FB_APP_SECRET")
    if not (obtained and app_id and secret and tok.get("access_token")):
        return False
    if now - datetime.fromisoformat(obtained) < timedelta(days=TOKEN_REFRESH_AFTER_DAYS):
        return False
    data = exchange_long_lived(app_id, secret, tok["access_token"], cfg.get("instagram.api_version", "v25.0"))
    store.save_token(access_token=data["access_token"], obtained_at=now.isoformat(timespec="seconds"),
                     expires_in=data.get("expires_in"))
    log.info("Refreshed the Instagram access token")
    return True


def post_one(cfg: Config, store: Store, recipe: Recipe, client: InstagramClient, now: datetime) -> dict[str, Any]:
    video = render_mod.render(cfg, recipe)
    video_url = None
    if cfg.get("instagram.upload_mode", "resumable") == "url":
        base = cfg.get("instagram.public_base_url")
        if not base:
            raise InstagramError("upload_mode is 'url' but instagram.public_base_url is not set")
        video_url = f"{base.rstrip('/')}/{video.name}"
    quote_seg = next((s for s in recipe.segments if s.role == "quote"), None)
    thumb_ms = int(((quote_seg.start + 1.5) if quote_seg else 1.0) * 1000)
    result = client.post_reel(video, recipe.caption, video_url=video_url,
                              share_to_feed=cfg.get("instagram.share_to_feed", True),
                              thumb_offset_ms=thumb_ms)
    store.record_post(recipe.id, result.media_id, result.container_id, result.permalink, now)
    store.update_state(consecutive_failures=0)
    return {"recipe_id": recipe.id, "media_id": result.media_id, "permalink": result.permalink}


def todays_plan(cfg: Config, store: Store, now: datetime) -> dict[str, Any]:
    st = store.state
    n = schedule.per_day(cfg.get("schedule.ramp", []), store.start_date, now.date(), cap=st.get("cap"))
    n = min(n, cfg.get("instagram.hard_daily_cap", 25))
    s = schedule.slots(now.date(), n, cfg.get("schedule.window_start", "07:00"),
                       cfg.get("schedule.window_end", "23:00"), cfg.get("schedule.jitter_minutes", 0))
    posted = len(store.posts_on(now.date()))
    return {"per_day": n, "slots": s, "posted_today": posted,
            "week": schedule.posting_week(store.start_date, now.date()) if store.start_date else None,
            "next_slot": schedule.next_slot(now, s, posted)}


def tick(cfg: Config, now: datetime | None = None, client: InstagramClient | None = None) -> str:
    """One run of the scheduled job: post if a slot is due, else render ahead."""
    now = now or datetime.now()
    store = Store(cfg.data_dir)
    with store.lock() as got:
        if not got:
            return "Another run is in progress."
        st = store.state
        if not store.start_date:
            return "Not started yet. Run `philoreels start` to go live."
        if st.get("paused"):
            return f"Paused: {st.get('pause_reason', 'by you')}. Run `philoreels resume` to continue."

        plan = todays_plan(cfg, store, now)
        due = schedule.should_post(now, plan["slots"], plan["posted_today"], store.last_post_time(),
                                   cfg.get("schedule.min_gap_minutes", 40))
        if not due:
            rendered = render_ahead(cfg, store, limit=2)
            nxt = plan["next_slot"].strftime("%H:%M") if plan["next_slot"] else "tomorrow"
            msg = f"Nothing due. {plan['posted_today']}/{plan['per_day']} posted today, next at {nxt}."
            if rendered:
                msg += f" Rendered {', '.join(rendered)}."
            return msg

        pending = queue(cfg, store)
        if not pending:
            notify("The reel backlog is empty. Add more with `philoreels generate`.")
            return "Backlog empty."
        recipe = pending[0]
        try:
            try:
                maybe_refresh_token(cfg, store, now)
            except InstagramError as e:
                log.warning("Token refresh failed: %s", e)
            client = client or make_client(cfg, store)
            out = post_one(cfg, store, recipe, client, now)
            return f"Posted reel {recipe.id}: {out.get('permalink') or out['media_id']}"
        except (InstagramError, render_mod.RenderError) as e:
            store.record_failure(recipe.id, str(e), now)
            fails = st.get("consecutive_failures", 0) + 1
            store.update_state(consecutive_failures=fails)
            limit = cfg.get("schedule.max_consecutive_failures", 3)
            if fails >= limit:
                store.update_state(paused=True, pause_reason=f"{fails} failures in a row; last: {e}")
                notify(f"Posting paused after {fails} failures. Last error: {e}")
            return f"Failed to post reel {recipe.id}: {e}"
