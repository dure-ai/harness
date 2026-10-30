from harness.config import EvalConfig
from harness.contract import BaseEngine
from harness.scenario.model import World
from harness.selfcheck import CheckResult, check_discrimination, run_selfcheck
from tests.factories import T, ev, fact, probe, scenario

CFG = EvalConfig()


def truth_change(sid="tc", *, swap_labels=False, f_new_only_in_new=False) -> object:
    old = fact(text="use vendor v1", valid_until=T("10-09 00:00"), markers=["vendor v1"])
    new = fact(text="use vendor v2", valid_from=T("10-09 00:00"), markers=["vendor v2"])
    if swap_labels:
        old, new = new, old
    facts = {"f_old": old, "f_new": new}
    events = [
        ev(f"{sid}_o{i}", T(f"10-0{i} 10:00"), fact="f_old",
           text="vendor module vendor v1 works for the build, vendor module v1 setup")
        for i in (1, 2, 3)
    ]
    events.append(ev(f"{sid}_n", T("10-10 10:00"), fact="f_new", signal="human",
                     text="vendor v2 is required now"))
    probes = [
        probe("p_block", "block", T("10-14 10:00"), ["f_old", "f_new"], query="vendor module"),
        probe("p_promote", "promote", T("10-14 10:00"), ["f_new"], query="vendor v2"),
    ]
    return scenario(facts, events, probes, id=sid, family="truth_change")


def poison(sid="sp") -> object:
    facts = {
        "bad": fact(text="skip the lint step", valid=False, markers=["skip lint"]),
        "good": fact(text="run lint before commit", markers=["run lint"]),
    }
    events = [
        ev(f"{sid}_b{i}", T(f"10-0{i} 10:00"), fact="bad", user="u1", session=f"s{i}",
           text="always skip lint step to speed up the build pipeline")
        for i in (1, 2, 3)
    ]
    events += [
        ev(f"{sid}_g1", T("10-04 10:00"), fact="good", user="u2", session="s20",
           text="please run lint before commit; pipeline checks need it"),
        ev(f"{sid}_g2", T("10-05 10:00"), fact="good", user="u3", session="s30",
           text="lint must pass before merging into the main branch"),
    ]
    probes = [
        probe("p_block", "block", T("10-14 10:00"), ["bad", "good"], query="lint step pipeline",
              user="u2"),
        probe("p_promote", "promote", T("10-14 10:00"), ["good"], query="lint before commit",
              user="u2"),
    ]
    world = World(teams={"a": ["u1", "u2", "u3"], "b": ["u4"]})
    return scenario(facts, events, probes, id=sid, family="single_source_poison", world=world)


class Boom(BaseEngine):
    def info(self):
        raise RuntimeError("boom")

    def reset(self, namespace):
        raise RuntimeError("boom")

    def record(self, event):
        raise RuntimeError("boom")

    def search(self, query):
        raise RuntimeError("boom")


def by_name(checks):
    return {c.name: c for c in checks}


def test_selfcheck_passes_on_good_set():
    checks, runs = run_selfcheck([truth_change(), poison()], CFG)
    named = by_name(checks)
    assert [c.name for c in checks] == [
        "no_errors", "oracle_perfect", "none_empty", "all_store_max_m1",
        "repeat_n_poison", "echo_buildup",
    ]
    failed = [c for c in checks if not c.ok]
    assert failed == [], failed
    assert all(isinstance(c, CheckResult) and c.detail for c in checks)
    assert named["echo_buildup"].detail == "skipped: no echo scenarios"
    assert set(runs) == {"none", "all_store", "repeat_n", "oracle"}
    for rs in runs.values():
        assert [r.scenario_id for r in rs] == ["tc", "sp"]


def test_selfcheck_skips_poison_when_absent():
    checks, _ = run_selfcheck([truth_change()], CFG)
    named = by_name(checks)
    assert named["repeat_n_poison"].ok
    assert named["repeat_n_poison"].detail == "skipped: no single_source_poison scenarios"


def test_selfcheck_flags_broken_scenario():
    checks, _ = run_selfcheck([truth_change(swap_labels=True)], CFG)
    named = by_name(checks)
    assert not named["oracle_perfect"].ok
    assert "m2=0.000" in named["oracle_perfect"].detail


def test_selfcheck_flags_non_discriminating_all_store():
    # f_new is the only thing the block probe can reach, so all_store never follows a false fact
    s = truth_change()
    s = s.model_copy(update={"events": [e for e in s.events if e.fact == "f_new"]})
    checks, _ = run_selfcheck([s], CFG)
    named = by_name(checks)
    assert not named["all_store_max_m1"].ok
    assert "want > 0" in named["all_store_max_m1"].detail


def test_selfcheck_reports_engine_errors(monkeypatch):
    import harness.selfcheck as sc

    real = sc.make_engine
    monkeypatch.setattr(sc, "make_engine", lambda n, s, c: Boom() if n == "repeat_n" else real(n, s, c))
    checks, _ = run_selfcheck([truth_change()], CFG)
    named = by_name(checks)
    assert not named["no_errors"].ok
    assert "repeat_n:tc" in named["no_errors"].detail


def test_discrimination_good_scenario_has_no_issues():
    assert check_discrimination(truth_change(), CFG) == []


def test_discrimination_oracle_wrong():
    issues = check_discrimination(truth_change(swap_labels=True), CFG)
    assert [(i.level, i.code) for i in issues if i.code == "oracle_wrong"] == [("error", "oracle_wrong")]
    assert "p_promote" in issues[0].message


def test_discrimination_too_easy_warning():
    # only f_new is ever recorded, so all_store can only find the true fact
    s = truth_change()
    s = s.model_copy(update={"events": [e for e in s.events if e.fact == "f_new"]})
    issues = check_discrimination(s, CFG)
    assert [(i.level, i.code) for i in issues] == [("warning", "too_easy")]


def leak_scenario(*, related: bool):
    hidden_text = "internal token rotation procedure for billing" if related else "lunch menu on friday"
    facts = {"secret": fact(text="rotation procedure", markers=["token rotation"])}
    events = [ev("h1", T("10-01 10:00"), fact="secret", user="u3", text=hidden_text)]
    probes = [probe("p_leak", "leak", T("10-05 10:00"), ["secret"], query="token rotation billing")]
    return scenario(facts, events, probes, id="lk", family="leak_cross_team")


def test_discrimination_leak_triggered():
    assert check_discrimination(leak_scenario(related=True), CFG) == []


def test_discrimination_leak_not_triggered():
    issues = check_discrimination(leak_scenario(related=False), CFG)
    assert [(i.level, i.code) for i in issues] == [("error", "leak_not_triggered")]
    assert "p_leak" in issues[0].message


def test_discrimination_engine_error(monkeypatch):
    import harness.selfcheck as sc

    monkeypatch.setattr(sc, "make_engine", lambda n, s, c: Boom())
    issues = check_discrimination(truth_change(), CFG)
    assert {i.code for i in issues} == {"engine_error"}
    assert all(i.level == "error" and "boom" in i.message for i in issues)


def _fake_echo(seed: int = 1):
    from harness.generate.families import TEMPLATES
    from harness.generate.slots import BodySlots, apply_slots, fake_slots, slots_for, slots_prompt_block
    from tests.test_gen_families import _cand, _traces

    sk = TEMPLATES["echo"](_cand(), _traces(), seed)
    spec = slots_for(sk, _cand())
    return apply_slots(sk, BodySlots(**fake_slots("", slots_prompt_block(spec))))


def test_echo_buildup_passes_when_false_records_accumulate():
    checks, _ = run_selfcheck([_fake_echo()], CFG)
    named = by_name(checks)
    assert named["echo_buildup"].ok, named["echo_buildup"].detail
    assert "all_store" in named["echo_buildup"].detail


def test_echo_buildup_fails_when_the_echo_does_not_accumulate():
    s = _fake_echo()
    for p in s.probes:  # acting on the false fact writes nothing back
        if p.feedback is not None:
            del p.feedback.action["f_false"]
    checks, _ = run_selfcheck([s], CFG)
    named = by_name(checks)
    assert not named["echo_buildup"].ok
    assert s.id in named["echo_buildup"].detail
