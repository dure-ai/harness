from datetime import datetime, timezone

from harness.world import probe_ctx, true_at
from tests.factories import KST, T, fact, probe


def test_valid_from_inclusive_until_exclusive():
    f = fact(valid_from=T("10-09 00:00"), valid_until=T("10-12 00:00"))
    assert not true_at(f, T("10-08 23:59"), {})
    assert true_at(f, T("10-09 00:00"), {})
    assert true_at(f, T("10-11 23:59"), {})
    assert not true_at(f, T("10-12 00:00"), {})


def test_open_ended_intervals():
    assert true_at(fact(), T("10-10 00:00"), {})
    assert true_at(fact(valid_from=T("10-09 00:00")), T("12-01 00:00"), {})
    assert true_at(fact(valid_until=T("10-09 00:00")), T("01-01 00:00"), {})


def test_valid_false_never_true():
    assert not true_at(fact(valid=False), T("10-10 00:00"), {})


def test_conditions_must_all_match():
    f = fact(conditions={"service": "A"})
    assert true_at(f, T("10-10 00:00"), {"repo": "r", "service": "A"})
    assert not true_at(f, T("10-10 00:00"), {"repo": "r", "service": "B"})
    assert not true_at(f, T("10-10 00:00"), {"repo": "r"})  # 조건 키가 맥락에 없으면 거짓


def test_all_conditions_required():
    f = fact(conditions={"service": "A", "env": "prod"})
    assert true_at(f, T("10-10 00:00"), {"service": "A", "env": "prod"})
    assert not true_at(f, T("10-10 00:00"), {"service": "A", "env": "dev"})


def test_offset_independent():
    f = fact(valid_until=datetime(2026, 10, 9, 0, 0, tzinfo=KST))
    t_utc = datetime(2026, 10, 8, 15, 30, tzinfo=timezone.utc)  # = 10-09 00:30 KST
    assert not true_at(f, t_utc, {})
    assert true_at(f, datetime(2026, 10, 8, 14, 59, tzinfo=timezone.utc), {})


def test_probe_ctx_merges_repo_and_meta():
    p = probe("p", "block", T("10-10 00:00"), ["x"], repo="r1", meta={"service": "A"})
    assert probe_ctx(p) == {"repo": "r1", "service": "A"}
