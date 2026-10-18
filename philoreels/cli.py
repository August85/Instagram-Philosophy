"""Command line: `python -m philoreels <command>` (or `philoreels <command>`)."""
from __future__ import annotations

import argparse
import logging
import os
import plistlib
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from . import pipeline, render as render_mod
from .config import load_config
from .content import generate_recipes, load_quotes, load_recipes, write_recipes
from .instagram import InstagramError, exchange_long_lived, find_instagram_accounts
from .store import Store

LAUNCHD_LABEL = "com.dailyphilosophyforyou.reels"


def cmd_generate(cfg, args):
    recipes = generate_recipes(cfg.root, count=args.count, seed=args.seed, language=_lang(cfg))
    paths = write_recipes(cfg.root, recipes)
    print(f"Wrote {len(recipes)} reels into {len(paths)} files under reels/.")


def _lang(cfg) -> str:
    return cfg.get("content.language", "en")


def quotes_markdown(cfg) -> str:
    """The whole quote bank as one readable table, grouped by author (or chapter)."""
    from collections import Counter
    lang = _lang(cfg)
    quotes = load_quotes(cfg.root, lang)
    uses = Counter(r.quote_id for r in load_recipes(cfg.root))
    lines = [
        "# Quote index",
        "",
        f"All {len(quotes)} quotes the reels are built from. "
        "Generated from `content/quotes.yaml` by `philoreels quotes --markdown`; edit the YAML, not this file.",
        "",
        "**Checked** says whether the wording has been compared to a translation. "
        "**Reels** is how many of the reels in `reels/` use the quote.",
        "",
    ]
    groups: dict[str, list] = {}
    for q in quotes:
        title = f"Bhagavad Gita, chapter {q.ref.split('.')[0]}" if q.ref else q.author
        groups.setdefault(title, []).append(q)
    for title, group in groups.items():
        both = lang != "en"
        lines += [f"## {title} ({len(group)})", "",
                  "| ID | Quote | English | Source | Topic | Checked | Reels |" if both else "| ID | Quote | Source | Topic | Checked | Reels |",
                  "| --- | --- | --- | --- | --- | --- | --- |" if both else "| --- | --- | --- | --- | --- | --- |"]
        for q in group:
            text = q.text.replace("|", "\\|")
            if both:
                text += " | " + q.text_en.replace("|", "\\|")
            src = q.ref or q.work
            if q.speaker:
                src += f" ({q.speaker})"
            lines.append(f"| {q.id} | {text} | {src} | {q.topic} | {'yes' if q.checked else 'no'} | {uses.get(q.id, 0)} |")
        lines.append("")
    return "\n".join(lines)


def cmd_quotes(cfg, args):
    if args.markdown:
        out = cfg.root / "QUOTES.md"
        out.write_text(quotes_markdown(cfg))
        print(f"Wrote {out}")
        return
    quotes = load_quotes(cfg.root, _lang(cfg))
    shown = [q for q in quotes if not q.checked] if args.unchecked else quotes
    for q in shown:
        mark = "ok " if q.checked else "?? "
        print(f"{mark}{q.id}  {q.source}: {q.text}")
    print(f"\n{len(shown)} shown, {sum(q.checked for q in quotes)}/{len(quotes)} checked.")


def _pick(cfg, store, ids, count):
    recipes = load_recipes(cfg.root)
    if ids:
        wanted = set(ids)
        return [r for r in recipes if r.id in wanted]
    return pipeline.queue(cfg, store, recipes)[:count]


def cmd_render(cfg, args):
    store = Store(cfg.data_dir)
    todo = _pick(cfg, store, args.id, args.count)
    for i, r in enumerate(todo, 1):
        path = render_mod.render(cfg, r, force=args.force)
        print(f"[{i}/{len(todo)}] {r.id} -> {path}")


def cmd_preview(cfg, args):
    store = Store(cfg.data_dir)
    recipes = _pick(cfg, store, [args.id] if args.id else None, 1)
    if not recipes:
        sys.exit("No reel found.")
    r = recipes[0]
    video = render_mod.render(cfg, r, force=True)
    still = render_mod.cover_frame(cfg, video, render_mod.cover_time(video, r), video.with_suffix(".jpg"))
    print(f"Video: {video}\nStill: {still}\n\nCaption:\n{r.caption}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(video)])


def cmd_auth(cfg, args):
    store = Store(cfg.data_dir)
    version = cfg.get("instagram.api_version", "v25.0")
    token = args.token
    app_id, secret = os.environ.get("FB_APP_ID"), os.environ.get("FB_APP_SECRET")
    if app_id and secret:
        data = exchange_long_lived(app_id, secret, token, version)
        token = data["access_token"]
        print("Exchanged for a long-lived token (about 60 days; it is refreshed automatically).")
    else:
        print("FB_APP_ID / FB_APP_SECRET not set in .env, so the token is stored as given and will not auto-refresh.")
    accounts = find_instagram_accounts(token, version)
    if not accounts:
        sys.exit("No Instagram professional account is linked to a Facebook Page this token can see. See README step 2.")
    chosen = accounts[0]
    if args.username:
        match = [a for a in accounts if (a.get("username") or "").lower() == args.username.lower().lstrip("@")]
        if not match:
            sys.exit(f"@{args.username} not found. Seen: {', '.join('@' + str(a['username']) for a in accounts)}")
        chosen = match[0]
    store.save_token(access_token=token, ig_user_id=chosen["ig_user_id"], username=chosen.get("username"),
                     obtained_at=datetime.now().isoformat(timespec="seconds"))
    print(f"Saved. Posting to @{chosen.get('username')} (Page: {chosen['page']}).")


def cmd_check(cfg, args):
    ok = True
    def line(good, text):
        nonlocal ok
        ok &= good
        print(("OK   " if good else "FAIL ") + text)

    import shutil
    line(bool(shutil.which("ffmpeg")), "ffmpeg installed")
    for kind in ("serif_italic", "sans"):
        try:
            line(True, f"font {kind}: {cfg.font(kind)}")
        except FileNotFoundError as e:
            line(False, str(e))
    recipes = load_recipes(cfg.root)
    line(len(recipes) > 0, f"{len(recipes)} reels in reels/")
    store = Store(cfg.data_dir)
    line(True, f"data folder: {cfg.data_dir}")
    try:
        client = pipeline.make_client(cfg, store)
        acct = client.account()
        line(True, f"Instagram account @{acct.get('username')} ({acct.get('followers_count')} followers)")
        lim = client.publishing_limit()
        line(True, f"publishing quota used in last 24 h: {lim.get('quota_usage')} of {lim.get('config', {}).get('quota_total')}")
    except InstagramError as e:
        line(False, str(e))
    sys.exit(0 if ok else 1)


def cmd_post(cfg, args):
    store = Store(cfg.data_dir)
    todo = _pick(cfg, store, [args.id] if args.id else None, 1)
    if not todo:
        sys.exit("Nothing to post.")
    r = todo[0]
    if args.dry_run:
        path = render_mod.render(cfg, r)
        print(f"Would post {r.id} ({path}) with caption:\n\n{r.caption}")
        return
    out = pipeline.post_one(cfg, store, r, pipeline.make_client(cfg, store), datetime.now())
    print(f"Posted {r.id}: {out.get('permalink') or out['media_id']}")


def cmd_start(cfg, args):
    store = Store(cfg.data_dir)
    day = date.fromisoformat(args.date) if args.date else date.today()
    store.update_state(start_date=day.isoformat(), paused=False, consecutive_failures=0)
    print(f"Going live on {day}. Week 1 posts {cfg.get('schedule.ramp')[0]['per_day']} reels a day.")


def cmd_pause(cfg, args):
    Store(cfg.data_dir).update_state(paused=True, pause_reason="paused by you")
    print("Paused. Nothing will be posted until `philoreels resume`.")


def cmd_resume(cfg, args):
    Store(cfg.data_dir).update_state(paused=False, consecutive_failures=0, pause_reason=None)
    print("Resumed.")


def cmd_cap(cfg, args):
    store = Store(cfg.data_dir)
    store.update_state(cap=None if args.per_day <= 0 else args.per_day)
    print("Cap removed; following the ramp." if args.per_day <= 0 else f"Holding at most {args.per_day} reels a day.")


def cmd_status(cfg, args):
    store = Store(cfg.data_dir)
    now = datetime.now()
    st = store.state
    pending = pipeline.queue(cfg, store)
    print(f"Account:      {cfg.get('account.handle')}  (token: {'yes' if store.token else 'not set'})")
    print(f"Live since:   {st.get('start_date') or 'not started'}" + ("  [PAUSED: " + str(st.get('pause_reason')) + "]" if st.get("paused") else ""))
    if store.start_date:
        plan = pipeline.todays_plan(cfg, store, now)
        times = ", ".join(s.strftime("%H:%M") for s in plan["slots"])
        print(f"Today:        week {plan['week']}, {plan['posted_today']}/{plan['per_day']} posted. Slots: {times}")
        if st.get("cap"):
            print(f"Cap:          {st['cap']} a day (set with `philoreels cap`)")
    print(f"Posted total: {len(store.posts())}")
    print(f"Backlog:      {len(pending)} reels left, {pipeline.rendered_ahead(cfg, pending)} rendered and ready")
    for row in store.posts()[-5:]:
        print(f"  {row['posted_at']}  {row['recipe_id']}  {row.get('permalink') or row['media_id']}")


def cmd_fetch_images(cfg, args):
    from .images import contact_sheet, fetch
    folder = cfg.resolve("assets/backgrounds")
    saved = fetch(folder, limit=args.limit, sources=cfg.get("images.sources", ["openverse", "commons", "pixabay"]),
                  queries=cfg.get("images.queries"), reject_faces=cfg.get("images.reject_faces", True))
    sheet = contact_sheet(folder, cfg.data_dir / "backgrounds_contact_sheet.jpg")
    print(f"\nSaved {len(saved)} pictures in {folder} (credits in CREDITS.md).")
    print(f"Review them at a glance: {sheet}. Delete any you don't want.")


def cmd_voices(cfg, args):
    from .voice import list_elevenlabs_voices
    for v in list_elevenlabs_voices():
        print(f"{v['voice_id']}  {v['name']}  ({v['category']})")
    print("\nPut the id of the voice you want in render.elevenlabs.voice_id and set render.tts: elevenlabs.")


def cmd_voice_test(cfg, args):
    from . import voice
    out = voice.speak(cfg, args.text, cfg.data_dir / "voice-test.aiff")
    print(f"{voice.engine(cfg)} voice written to {out}")
    if args.play and sys.platform == "darwin":
        subprocess.run(["afplay", str(out)])


def cmd_make_music(cfg, args):
    from .music import make_library
    folder = cfg.resolve("assets/music")
    for path in make_library(folder, count=args.count, duration=args.seconds):
        print(f"Made {path}")


def cmd_tick(cfg, args):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {pipeline.tick(cfg)}", flush=True)


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def cmd_install_schedule(cfg, args):
    data_dir = cfg.data_dir
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, "-m", "philoreels", "tick"],
        "WorkingDirectory": str(cfg.root),
        "StartInterval": args.every * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(data_dir / "logs" / "tick.log"),
        "StandardErrorPath": str(data_dir / "logs" / "tick.err.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                 "PHILOREELS_ROOT": str(cfg.root)},
    }
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)
    subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    print(f"Installed {path}. The job runs every {args.every} minutes; logs in {data_dir / 'logs'}.")


def cmd_uninstall_schedule(cfg, args):
    path = _plist_path()
    subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
    path.unlink(missing_ok=True)
    print("Scheduled job removed.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="philoreels", description="Philosophy reels: generate, render and post to Instagram.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("generate", help="Write reel recipes into reels/ from the quote bank")
    s.add_argument("--count", type=int, default=1000)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_generate)

    s = sub.add_parser("quotes", help="List quotes (use --unchecked to see ones still to verify)")
    s.add_argument("--unchecked", action="store_true")
    s.add_argument("--markdown", action="store_true", help="Write QUOTES.md, a one-page index of every quote")
    s.set_defaults(fn=cmd_quotes)

    s = sub.add_parser("render", help="Render the next reels in the queue (or specific ids)")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--id", action="append")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("preview", help="Render one reel and print its caption")
    s.add_argument("--id")
    s.add_argument("--open", action="store_true", help="Open the video when done (macOS)")
    s.set_defaults(fn=cmd_preview)

    s = sub.add_parser("auth", help="Save an Instagram access token and find the account id")
    s.add_argument("--token", required=True, help="User token from the Graph API Explorer")
    s.add_argument("--username", help="Instagram username to post to, if the token sees several")
    s.set_defaults(fn=cmd_auth)

    sub.add_parser("check", help="Check ffmpeg, fonts, reels and the Instagram connection").set_defaults(fn=cmd_check)

    s = sub.add_parser("post", help="Post the next reel right now (or --id)")
    s.add_argument("--id")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_post)

    s = sub.add_parser("start", help="Go live: week 1 of the ramp starts today (or --date)")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_start)

    sub.add_parser("pause", help="Stop automatic posting").set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="Restart automatic posting").set_defaults(fn=cmd_resume)

    s = sub.add_parser("cap", help="Hold at N reels a day even if the ramp says more (0 removes the cap)")
    s.add_argument("per_day", type=int)
    s.set_defaults(fn=cmd_cap)

    sub.add_parser("status", help="Show today's plan, backlog and recent posts").set_defaults(fn=cmd_status)
    s = sub.add_parser("voices", help="List your ElevenLabs voices and their ids")
    s.set_defaults(fn=cmd_voices)

    s = sub.add_parser("voice-test", help="Speak a line with the configured voice")
    s.add_argument("text", nargs="?", default="तुम्हारा अधिकार केवल कर्म पर है, उसके फल पर कभी नहीं।")
    s.add_argument("--play", action="store_true")
    s.set_defaults(fn=cmd_voice_test)

    s = sub.add_parser("fetch-images", help="Download free, face-free background pictures into assets/backgrounds")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_fetch_images)

    s = sub.add_parser("make-music", help="Generate royalty-free flute and tanpura tracks into assets/music")
    s.add_argument("--count", type=int, default=8)
    s.add_argument("--seconds", type=float, default=75.0)
    s.set_defaults(fn=cmd_make_music)

    sub.add_parser("tick", help="One scheduled run (what the background job calls)").set_defaults(fn=cmd_tick)

    s = sub.add_parser("install-schedule", help="Run `tick` in the background on this Mac (launchd)")
    s.add_argument("--every", type=int, default=10, help="Minutes between runs")
    s.set_defaults(fn=cmd_install_schedule)
    sub.add_parser("uninstall-schedule", help="Remove the background job").set_defaults(fn=cmd_uninstall_schedule)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
