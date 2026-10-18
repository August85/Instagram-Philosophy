from collections import Counter

from philoreels.content import (FORMATS, generate_recipes, load_quotes, load_recipes, load_topics,
                                plan_order, validate_content)
from conftest import ROOT


def test_quote_bank_is_valid():
    quotes = load_quotes(ROOT)
    assert len(quotes) >= 100
    assert validate_content(quotes, load_topics(ROOT)) == []


def test_order_spreads_repeats_and_authors():
    quotes = load_quotes(ROOT)
    order = plan_order(quotes, 1000, min_gap=80)
    last, gaps = {}, []
    for i, q in enumerate(order):
        if q.id in last:
            gaps.append(i - last[q.id])
        last[q.id] = i
        if i:
            assert q.author != order[i - 1].author
    assert min(gaps) > 80
    counts = Counter(q.id for q in order)
    assert max(counts.values()) - min(counts.values()) <= 1


def test_generated_recipes_are_complete():
    recipes = generate_recipes(ROOT, count=200)
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
