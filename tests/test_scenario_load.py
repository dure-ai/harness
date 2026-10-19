from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.contract import Query
from harness.scenario.load import ScenarioLoadError, load_scenario, load_set
from harness.scenario.model import FAMILIES, FEEDBACK_FAMILIES
from tests.factories import KST, T, ev, fact, probe, scenario

EXAMPLE_YAML = """\
id: vendor-module-ownership-001
family: truth_change
mode: read_only
language: en
world:
  timezone: Asia/Seoul
  teams:
    payments: [u1, u2]
    search: [u3]
facts:
  f_old:
    text: "The vendor module is owned by another team; do not modify it"
    valid_until: "2026-10-09T00:00+09:00"
    markers: ["do not modify", "owned by another team"]
  f_new:
    text: "Our team now modifies the vendor module directly"
    valid_from: "2026-10-09T00:00+09:00"
    markers: ["merged directly", "fix it ourselves"]
events:
  - {id: e1, t: "2026-10-01T10:00+09:00", user: u1, session: s1, repo: shop-api,
     scope: team, signal: self, kind: agent_record, outcome: unknown, fact: f_old,
     text: "Vendor module is owned by another team, so do not modify it"}
  - {id: e2, t: "2026-10-09T14:00+09:00", user: u1, session: s9, repo: shop-api,
     scope: team, signal: human, kind: human_confirm, outcome: success, fact: f_new,
     text: "Merged the vendor module PR directly"}
probes:
  - {id: p_block, kind: block, t: "2026-10-14T10:00+09:00", user: u2, session: s20,
     repo: shop-api, query: "Can I fix the stock bug in the vendor module?",
     about: [f_old, f_new]}
  - {id: p_promote, kind: promote, t: "2026-10-08T10:00+09:00", user: u2, session: s5,
     repo: shop-api, query: "Can I modify the vendor module?", about: [f_old]}
review:
  status: draft
  reviewer: null
  notes: ""
provenance:
  author: llm
  source_traces: ["nvidia/Open-SWE-Traces:v1.0:<trajectory_id>"]
  llm_model: null
  prompt_version: null
"""


def write(dir: Path, text: str, name: str = "s.yaml") -> Path:
    p = dir / name
    p.write_text(text, encoding="utf-8")
    return p


def with_status(status: str, id: str) -> str:
    return EXAMPLE_YAML.replace("status: draft", f"status: {status}").replace(
        "id: vendor-module-ownership-001", f"id: {id}"
    )


def test_constants():
    assert len(FAMILIES) == 10
    assert FEEDBACK_FAMILIES == ("echo", "failure_feedback")


def test_load_example_yaml(tmp_path):
    s = load_scenario(write(tmp_path, EXAMPLE_YAML))
    assert s.facts["f_old"].valid_until == datetime(2026, 10, 9, tzinfo=KST)
    assert s.labeled_events()[0].event.team == "payments"
    assert s.labeled_events()[0].fact == "f_old"
    assert [p.id for p in s.probes] == ["p_block", "p_promote"]


def test_naive_time_is_error_with_path(tmp_path):
    p = write(tmp_path, EXAMPLE_YAML.replace("2026-10-01T10:00+09:00", "2026-10-01 10:00"))
    with pytest.raises(ScenarioLoadError) as e:
        load_scenario(p)
    assert p.name in str(e.value)


def test_mixed_offsets_compare(tmp_path):
    text = EXAMPLE_YAML.replace("2026-10-01T10:00+09:00", "2026-10-01T00:30Z")
    s = load_scenario(write(tmp_path, text))
    # 00:30Z is 09:30 KST, before e2
    assert s.events[0].t.utcoffset() == timezone.utc.utcoffset(None)
    assert s.events[0].t < s.events[1].t


def test_malformed_yaml_is_load_error(tmp_path):
    p = write(tmp_path, "id: [unclosed")
    with pytest.raises(ScenarioLoadError) as e:
        load_scenario(p)
    assert p.name in str(e.value)


def test_load_set_filters_review(tmp_path):
    write(tmp_path, with_status("approved", "a"), "a.yaml")
    write(tmp_path, with_status("draft", "b"), "b.yaml")
    write(tmp_path, with_status("rejected", "c"), "c.yaml")
    assert len(load_set([tmp_path], include_drafts=False).scenarios) == 1
    ss = load_set([tmp_path], include_drafts=True)
    assert len(ss.scenarios) == 2
    assert [p.name for p in ss.paths] == ["a.yaml", "b.yaml"]


def test_load_set_recurses_in_sorted_order(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path, with_status("approved", "z"), "z.yaml")
    write(tmp_path / "sub", with_status("approved", "m"), "m.yaml")
    write(tmp_path, with_status("approved", "a"), "a.yaml")
    ss = load_set([tmp_path], include_drafts=False)
    assert [s.id for s in ss.scenarios] == ["a", "m", "z"]


def test_set_hash_changes_with_content(tmp_path):
    p = write(tmp_path, with_status("approved", "a"))
    h1 = load_set([tmp_path], include_drafts=False).hash
    assert load_set([tmp_path], include_drafts=False).hash == h1
    p.write_text(with_status("approved", "a") + "\n# edited\n", encoding="utf-8")
    assert load_set([tmp_path], include_drafts=False).hash != h1


def test_set_hash_ignores_excluded_files(tmp_path):
    write(tmp_path, with_status("approved", "a"), "a.yaml")
    h1 = load_set([tmp_path], include_drafts=False).hash
    write(tmp_path, with_status("draft", "b"), "b.yaml")
    assert load_set([tmp_path], include_drafts=False).hash == h1
    assert load_set([tmp_path], include_drafts=True).hash != h1


def test_query_for_hides_labels():
    sc = scenario(
        {"f": fact()},
        [ev("e1", T("10-01 10:00"), fact="f")],
        [probe("p", "block", T("10-14 10:00"), ["f"])],
    )
    q = sc.query_for(sc.probes[0], k=5)
    assert set(Query.model_fields) == {"text", "user", "team", "session", "repo", "t", "k", "meta"}
    assert q.team == "a" and q.k == 5
    assert sc.query_for(probe("p2", "block", T("10-14 10:00"), ["f"], k=3), k=5).k == 3


def test_team_of_unknown_user_raises():
    sc = scenario({}, [], [])
    assert sc.team_of("u3") == "b"
    with pytest.raises(KeyError):
        sc.team_of("nobody")


def test_labeled_events_keep_order_and_explicit_team():
    sc = scenario(
        {"f": fact()},
        [
            ev("e1", T("10-01 10:00"), user="u3", fact="f", secret_markers=["x"]),
            ev("e2", T("10-02 10:00"), team="b"),
        ],
        [],
    )
    le = sc.labeled_events()
    assert [x.event.id for x in le] == ["e1", "e2"]
    assert le[0].event.team == "b" and le[0].secret_markers == ("x",)
    assert le[1].event.team == "b" and le[1].fact is None
    assert not hasattr(le[0].event, "fact")


@pytest.mark.parametrize(
    "old,new",
    [
        ("valid_until:", "valid_untill:"),  # fact
        ("fact: f_old,", "fact: f_old, secret_marker: [x],"),  # event
        ("about: [f_old]}", "about: [f_old], feedbak: null}"),  # probe
        ("language: en", "language: en\nlanguge: ko"),  # scenario
        ("  timezone: Asia/Seoul", "  timezone: Asia/Seoul\n  timezon: x"),  # world
        ("  notes: \"\"", "  notes: \"\"\n  reviewr: me"),  # review
        ("  llm_model: null", "  llm_model: null\n  llm_modle: x"),  # provenance
    ],
)
def test_unknown_key_is_error_with_path(tmp_path, old, new):
    assert old in EXAMPLE_YAML
    p = write(tmp_path, EXAMPLE_YAML.replace(old, new, 1))
    with pytest.raises(ScenarioLoadError) as e:
        load_scenario(p)
    assert p.name in str(e.value)


def test_unknown_key_in_feedback_models_is_error():
    from pydantic import ValidationError

    from harness.scenario.model import Feedback, FeedbackTemplate

    with pytest.raises(ValidationError):
        FeedbackTemplate(text="x", delya=None)
    with pytest.raises(ValidationError):
        Feedback(abstian=None)
