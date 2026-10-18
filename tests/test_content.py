from collections import Counter

from philoreels.content import (FORMATS, generate_recipes, load_quotes, load_recipes, load_topics,
                                plan_order, validate_content)
from conftest import ROOT


def test_quote_bank_is_valid():
    quotes = load_quotes(ROOT)
    assert len(quotes) >= 100
    assert validate_content(quotes, load_topics(ROOT)) == []


def test_order_spreads_repeats_and_chapters():
    quotes = load_quotes(ROOT)
    order = plan_order(quotes, 1000, min_gap=80)
    last, gaps = {}, []
    for i, q in enumerate(order):
        if q.id in last:
            gaps.append(i - last[q.id])
        last[q.id] = i
        if i:
            assert q.group != order[i - 1].group
    assert min(gaps) > 80
    counts = Counter(q.id for q in order)
    assert max(counts.values()) - min(counts.values()) <= 1


def test_generated_recipes_are_complete():
    recipes = generate_recipes(ROOT, count=200, language="hi")
    assert [r.id for r in recipes[:3]] == ["0001", "0002", "0003"]
    assert {r.format for r in recipes} == set(FORMATS)
    for r in recipes:
        assert 7 <= r.duration <= 25
        roles = [s.role for s in r.segments]
        assert "quote" in roles and "author" in roles
        assert all(0 <= s.start < s.end <= r.duration + 1e-6 for s in r.segments)
        assert len(r.caption) < 2200  # Instagram caption limit
        assert r.caption.count("#") <= 30


def test_committed_backlog_has_1000_reels():
    recipes = load_recipes(ROOT)
    assert len(recipes) == 1000
    assert len({r.id for r in recipes}) == 1000


def test_hindi_bank_covers_every_verse():
    quotes = load_quotes(ROOT, "hi")
    assert {q.author for q in quotes} == {"भगवद्गीता"}
    q = next(q for q in quotes if q.ref == "2.47")
    assert q.text.startswith("तुम्हारा अधिकार") and q.text_en.startswith("You have a right")
    assert q.credit == "भगवद्गीता 2.47"
    topics_en, topics_hi = load_topics(ROOT), load_topics(ROOT, "hi")
    for name, t in topics_en["topics"].items():
        assert len(topics_hi["topics"][name]["hooks"]) == len(t["hooks"])
        assert any("\u0900" <= ch <= "\u097f" for ch in topics_hi["topics"][name]["hooks"][0])


def test_committed_reels_are_in_hindi():
    r = load_recipes(ROOT)[0]
    quote = next(s for s in r.segments if s.role == "quote")
    assert any("\u0900" <= ch <= "\u097f" for ch in quote.text)
    assert "#bhagavadgita" in r.caption and "भगवद्गीता" in r.caption


def test_gita_bank_uses_defaults():
    quotes = load_quotes(ROOT)
    assert {q.author for q in quotes} == {"Bhagavad Gita"}
    q = next(q for q in quotes if q.ref == "2.47")
    assert q.credit == "Bhagavad Gita 2.47"
    assert "#bhagavadgita" in q.hashtags
    assert q.group == "Bhagavad Gita:2"
