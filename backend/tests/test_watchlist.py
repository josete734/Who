"""Watchlist diff-hash tests."""
from __future__ import annotations

from app.watchlist.model import diff_findings, hash_findings


def test_hash_is_stable_across_runs():
    f = [{"fingerprint": "a"}, {"fingerprint": "b"}]
    assert hash_findings(f) == hash_findings(f)


def test_hash_is_order_independent():
    a = [{"fingerprint": "a"}, {"fingerprint": "b"}, {"fingerprint": "c"}]
    b = list(reversed(a))
    assert hash_findings(a) == hash_findings(b)


def test_hash_changes_when_finding_added():
    base = [{"fingerprint": "a"}, {"fingerprint": "b"}]
    new = base + [{"fingerprint": "c"}]
    assert hash_findings(base) != hash_findings(new)


def test_hash_changes_when_finding_removed():
    base = [{"fingerprint": "a"}, {"fingerprint": "b"}]
    smaller = [{"fingerprint": "a"}]
    assert hash_findings(base) != hash_findings(smaller)


def test_hash_dedupes_identical_fingerprints():
    a = [{"fingerprint": "x"}]
    b = [{"fingerprint": "x"}, {"fingerprint": "x"}]
    assert hash_findings(a) == hash_findings(b)


def test_diff_findings_detects_change():
    prev_hash = hash_findings([{"fingerprint": "a"}])
    new_hash, changed = diff_findings(prev_hash, [{"fingerprint": "a"}, {"fingerprint": "b"}])
    assert changed is True
    assert new_hash != prev_hash


def test_diff_findings_no_change_when_identical():
    findings = [{"fingerprint": "a"}, {"fingerprint": "b"}]
    prev_hash = hash_findings(findings)
    new_hash, changed = diff_findings(prev_hash, findings)
    assert changed is False
    assert new_hash == prev_hash


def test_diff_findings_first_run_always_changed():
    new_hash, changed = diff_findings(None, [{"fingerprint": "a"}])
    assert changed is True
    assert isinstance(new_hash, str) and len(new_hash) == 64


def test_hash_falls_back_for_dicts_without_fingerprint():
    a = [{"title": "t1", "url": "u1"}]
    b = [{"title": "t1", "url": "u1"}]
    assert hash_findings(a) == hash_findings(b)
    c = [{"title": "t1", "url": "u2"}]
    assert hash_findings(a) != hash_findings(c)
