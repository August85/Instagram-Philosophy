from datetime import datetime, timedelta

from philoreels import pipeline
from philoreels.instagram import InstagramError, PublishResult
from philoreels.store import Store


class FakeClient:
    def __init__(self, fail=False):
        self.posted, self.fail = [], fail

    def post_reel(self, video, caption, video_url=None, share_to_feed=True, thumb_offset_ms=None):
        if self.fail:
            raise InstagramError("boom")
        self.posted.append(video.name)
        return PublishResult("C", f"M{len(self.posted)}", None)


def fake_render(monkeypatch, cfg):
    def render(cfg_, recipe, force=False):
        p = pipeline.render_mod.video_path(cfg_, recipe.id)
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(pipeline.render_mod, "render", render)


def test_not_started_does_nothing(cfg):
    assert "Not started" in pipeline.tick(cfg, now=datetime(2026, 10, 20, 12), client=FakeClient())


def test_two_posts_on_day_one(cfg, monkeypatch):
    fake_render(monkeypatch, cfg)
    store = Store(cfg.data_dir)
    store.update_state(start_date="2026-10-20")
    client = FakeClient()
    t = datetime(2026, 10, 20, 6, 0)
    while t < datetime(2026, 10, 21, 0, 0):
        pipeline.tick(cfg, now=t, client=client)
        t += timedelta(minutes=10)
    assert client.posted == ["0001.mp4", "0002.mp4"]
    assert len(store.posts_on(datetime(2026, 10, 20).date())) == 2


def test_week_seven_posts_ten(cfg, monkeypatch):
    fake_render(monkeypatch, cfg)
    store = Store(cfg.data_dir)
    store.update_state(start_date="2026-10-20")
    client = FakeClient()
    day = datetime(2026, 12, 1)  # week 7
    t = day.replace(hour=6)
    while t < day + timedelta(days=1):
        pipeline.tick(cfg, now=t, client=client)
        t += timedelta(minutes=10)
    assert len(client.posted) == 10


def test_pauses_after_repeated_failures(cfg, monkeypatch):
    fake_render(monkeypatch, cfg)
    monkeypatch.setattr(pipeline, "notify", lambda m: None)
    store = Store(cfg.data_dir)
    store.update_state(start_date="2026-10-20")
    t = datetime(2026, 10, 20, 23, 0)  # both slots already due
    for i in range(3):
        pipeline.tick(cfg, now=t + timedelta(minutes=i), client=FakeClient(fail=True))
    assert store.state["paused"] is True
    assert "Paused" in pipeline.tick(cfg, now=t + timedelta(minutes=5), client=FakeClient())
    # A reel that failed twice is skipped, not retried forever.
    assert pipeline.queue(cfg, store)[0].id == "0002"
