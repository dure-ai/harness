import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

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
    write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
    write(tmp_path, with_status("draft", "b"), "b.yaml")
    write(tmp_path, with_status("rejected", "c"), "c.yaml")
    assert len(load_set([tmp_path], include_drafts=False).scenarios) == 1
    ss = load_set([tmp_path], include_drafts=True)
    assert len(ss.scenarios) == 2
    assert [p.name for p in ss.paths] == ["a.yaml", "b.yaml"]


def test_load_set_recurses_in_sorted_order(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path, as_human(with_status("approved", "z")), "z.yaml")
    write(tmp_path / "sub", as_human(with_status("approved", "m")), "m.yaml")
    write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
    ss = load_set([tmp_path], include_drafts=False)
    assert [s.id for s in ss.scenarios] == ["a", "m", "z"]


def test_set_hash_changes_with_content(tmp_path):
    p = write(tmp_path, as_human(with_status("approved", "a")))
    h1 = load_set([tmp_path], include_drafts=False).hash
    assert load_set([tmp_path], include_drafts=False).hash == h1
    p.write_text(as_human(with_status("approved", "a")) + "\n# edited\n", encoding="utf-8")
    assert load_set([tmp_path], include_drafts=False).hash != h1


def test_set_hash_ignores_excluded_files(tmp_path):
    write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
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


def test_labeled_events_carry_refs():
    sc = scenario(
        {},
        [ev("e1", T("10-01 10:00")), ev("e2", T("10-02 10:00"), kind="ci_result", refs=["e1"])],
        [],
    )
    events = [le.event for le in sc.labeled_events()]
    assert events[0].refs == () and events[1].refs == ("e1",)


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("mode: read_only\n", "mode: read_only\nmode: feedback\n", "mode"),
        ("facts:\n", 'facts:\n  f_old:\n    text: "FIRST definition"\n', "f_old"),
    ],
)
def test_duplicate_yaml_key_is_a_load_error(tmp_path, old, new, key):
    p = write(tmp_path, EXAMPLE_YAML.replace(old, new, 1))
    with pytest.raises(ScenarioLoadError) as e:
        load_scenario(p)
    assert p.name in str(e.value)
    assert "duplicate" in e.value.message and repr(key) in e.value.message and "line" in e.value.message


def test_duplicate_yaml_key_in_sealed_cli_hides_path(tmp_path):
    from typer.testing import CliRunner

    from harness.cli import app

    d = tmp_path / "scenarios"
    d.mkdir()
    write(d, EXAMPLE_YAML.replace("mode: read_only\n", "mode: read_only\nmode: feedback\n", 1), "secret-name.yaml")
    result = CliRunner().invoke(app, ["selfcheck", "--scenarios", str(d), "--sealed"])
    assert result.exit_code == 1
    assert "secret-name" not in result.output and "mode" not in result.output
    assert "error load" in result.output


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "", ".hidden", "-lead", "has space", "x" * 121, "a:b", "a\nb"])
def test_scenario_id_with_path_characters_is_rejected(bad):
    with pytest.raises(ValueError):
        scenario({}, [], [], id=bad)


@pytest.mark.parametrize("good", ["a", "A1", "vendor-module-ownership-001", "a.b_c-d", "x" * 120])
def test_scenario_id_accepts_plain_ids(good):
    assert scenario({}, [], [], id=good).id == good


def test_explicit_probe_k_is_honoured_and_k_below_one_rejected():
    from pydantic import ValidationError

    from harness.scenario.model import Probe

    sc = scenario({"f": fact()}, [], [probe("p", "block", T("10-14 10:00"), ["f"])])
    assert sc.query_for(probe("p", "block", T("10-14 10:00"), ["f"], k=1), k=5).k == 1
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            probe("p", "block", T("10-14 10:00"), ["f"], k=bad)
    assert Probe.model_fields["k"].default is None


PROVENANCE_EXTRA = """\
  provider: anthropic
  source_repos:
    - {repo: acme/shop, license: MIT}
  fabricated_facts: [f_new]
  evidence:
    - {event_id: e1, trace_id: tr1, message_index: 3, quote: "do not modify", origin: quoted}
    - {event_id: e2, trace_id: null, message_index: null, quote: null, origin: written}
  reviewer_edited: true
  generated_at: "2026-10-20T09:00+09:00"
"""


def test_provenance_new_fields_roundtrip(tmp_path):
    text = EXAMPLE_YAML.replace("  llm_model: null\n", "  llm_model: null\n" + PROVENANCE_EXTRA)
    s = load_scenario(write(tmp_path, text))
    prov = s.provenance
    assert prov.provider == "anthropic"
    assert [(r.repo, r.license) for r in prov.source_repos] == [("acme/shop", "MIT")]
    assert prov.fabricated_facts == ["f_new"]
    assert prov.evidence[0].quote == "do not modify" and prov.evidence[0].origin == "quoted"
    assert prov.evidence[1].origin == "written" and prov.evidence[1].trace_id is None
    assert prov.reviewer_edited is True
    assert prov.generated_at == datetime(2026, 10, 20, 9, tzinfo=KST)
    again = load_scenario(
        write(tmp_path, yaml.safe_dump(s.model_dump(mode="json")), "again.yaml")
    )
    assert again == s


def test_provenance_new_fields_default_empty(tmp_path):
    prov = load_scenario(write(tmp_path, EXAMPLE_YAML)).provenance
    assert prov.provider is None and prov.source_repos == [] and prov.evidence == []
    assert prov.fabricated_facts == [] and prov.reviewer_edited is False
    assert prov.generated_at is None


@pytest.mark.parametrize(
    "old,new",
    [
        ("license: MIT}", "licence: MIT}"),  # SourceRepo
        ("origin: written}", "origin: written, extra: 1}"),  # Evidence
    ],
)
def test_unknown_key_in_new_provenance_models_is_error(tmp_path, old, new):
    base = EXAMPLE_YAML.replace("  llm_model: null\n", "  llm_model: null\n" + PROVENANCE_EXTRA)
    assert old in base
    with pytest.raises(ScenarioLoadError):
        load_scenario(write(tmp_path, base.replace(old, new, 1)))


def test_provenance_not_in_engine_events(tmp_path):
    text = EXAMPLE_YAML.replace("  llm_model: null\n", "  llm_model: null\n" + PROVENANCE_EXTRA)
    s = load_scenario(write(tmp_path, text))
    for le in s.labeled_events():
        dumped = le.event.__dict__
        assert not hasattr(le.event, "provenance")
        assert "provenance" not in dumped and "evidence" not in dumped
        assert "quoted" not in repr(le.event) and "acme/shop" not in repr(le.event)
    for p in s.probes:
        q = repr(s.query_for(p, 5))
        assert "acme/shop" not in q and "provenance" not in q


def test_core_cli_imports_without_gen_deps():
    code = (
        "import sys\n"
        "for n in ('anthropic', 'openai', 'pyarrow', 'huggingface_hub'):\n"
        "    sys.modules[n] = None\n"
        "import harness.cli\n"
        "import harness.generate.config\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---- approval integrity ---------------------------------------------------------------------------


def approved_with_hash(dir: Path, id: str = "a", name: str = "a.yaml") -> Path:
    """An approved file whose `review.approved_hash` matches its content, as `review set` writes it."""
    from harness.scenario.labels import content_hash
    from harness.yamlio import load_yaml

    raw = load_yaml(with_status("approved", id))
    raw["review"]["approved_hash"] = content_hash(raw, "approval")
    return write(dir, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), name)


def test_approved_file_with_a_matching_hash_loads_without_warnings(tmp_path):
    approved_with_hash(tmp_path)
    ss = load_set([tmp_path], include_drafts=False)
    assert [s.id for s in ss.scenarios] == ["a"] and ss.warnings == []


def test_body_edit_after_approval_is_an_error_at_load(tmp_path):
    p = approved_with_hash(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("Merged the vendor module PR directly", "Merged it; vendor rumor"), encoding="utf-8")
    for drafts in (False, True):
        with pytest.raises(ScenarioLoadError) as e:
            load_set([tmp_path], include_drafts=drafts)
        assert e.value.code == "approved_content_changed"
        assert e.value.path == p and "approve" in e.value.message
    load_scenario(p)  # the file itself still reads; only the approved set refuses it


def test_label_edit_after_approval_is_an_error_at_load(tmp_path):
    p = approved_with_hash(tmp_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["facts"]["f_old"]["valid_until"] = "2026-10-10T00:00+09:00"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ScenarioLoadError) as e:
        load_set([tmp_path], include_drafts=False)
    assert e.value.code == "approved_content_changed"


def test_editing_only_the_review_block_or_comments_keeps_the_approval(tmp_path):
    p = approved_with_hash(tmp_path)
    p.write_text("# a comment\n" + p.read_text(encoding="utf-8").replace('notes: ""', 'notes: "looks fine"').replace("notes: ''", "notes: looks fine"), encoding="utf-8")
    assert load_set([tmp_path], include_drafts=False).warnings == []


def test_adding_a_defaulted_schema_field_does_not_invalidate_approvals(tmp_path):
    """The hash is of the parsed file, not of the model: fields the file omits (or that a later
    schema adds with a default) do not move it."""
    from harness.scenario.labels import content_hash

    p = approved_with_hash(tmp_path)
    s = load_scenario(p)
    # The model fills in defaults the file never wrote (event meta/refs/team, fact conditions, ...),
    # so a hash of the model dump would differ from the stored one; the stored one still verifies.
    assert content_hash(s.model_dump(mode="json"), "approval") != s.review.approved_hash
    ss = load_set([tmp_path], include_drafts=False)
    assert ss.warnings == [] and [x.id for x in ss.scenarios] == ["a"]


def test_old_approved_file_without_hash_loads_with_warning(tmp_path):
    p = write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
    ss = load_set([tmp_path], include_drafts=False)
    assert [s.id for s in ss.scenarios] == ["a"]
    assert len(ss.warnings) == 1 and "approval_unverified" in ss.warnings[0] and str(p) in ss.warnings[0]


def test_unknown_hash_version_is_unverified_not_an_error(tmp_path):
    text = as_human(with_status("approved", "a")).replace('notes: ""', 'notes: ""\n  approved_hash: "v9:0123"')
    write(tmp_path, text, "a.yaml")
    ss = load_set([tmp_path], include_drafts=False)
    assert len(ss.scenarios) == 1 and len(ss.warnings) == 1 and "approval_unverified" in ss.warnings[0]


def test_scenario_set_carries_approval_warnings(tmp_path):
    approved_with_hash(tmp_path, "ok", "ok.yaml")
    write(tmp_path, as_human(with_status("approved", "old")), "old.yaml")
    write(tmp_path, with_status("draft", "d"), "d.yaml")
    write(tmp_path, with_status("rejected", "r"), "r.yaml")
    ss = load_set([tmp_path], include_drafts=True)
    assert [s.id for s in ss.scenarios] == ["d", "ok", "old"]
    assert len(ss.warnings) == 1 and "old.yaml" in ss.warnings[0]  # drafts and rejected are not approvals


def test_files_without_any_new_field_load_without_error(tmp_path):
    """Files written before the hash fields existed: drafts load silently, approved ones warn only."""
    write(tmp_path, with_status("draft", "d"), "d.yaml")
    write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
    s = load_scenario(tmp_path / "a.yaml")
    assert s.review.approved_hash is None
    assert s.provenance.label_fingerprint is None and s.provenance.generated_hash is None
    ss = load_set([tmp_path], include_drafts=True)
    assert len(ss.scenarios) == 2 and len(ss.warnings) == 1


def approved_by_hand(dir: Path, **provenance: str) -> Path:
    """An approved file with the given harness-written provenance fields and no `approved_hash`."""
    from harness.yamlio import load_yaml

    raw = load_yaml(as_human(with_status("approved", "a")))
    raw["provenance"].update(provenance)
    return write(dir, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), "a.yaml")


@pytest.mark.parametrize(
    "fields",
    [{"generated_hash": "v1:" + "0" * 64}, {"label_fingerprint": "l1:" + "0" * 64}, {"generated_hash": "v1:x", "label_fingerprint": "l1:y"}],
)
def test_hand_approval_of_a_generated_file_is_an_error(tmp_path, fields):
    p = approved_by_hand(tmp_path, **fields)
    for drafts in (False, True):
        with pytest.raises(ScenarioLoadError) as e:
            load_set([tmp_path], include_drafts=drafts)
        assert e.value.code == "approved_hash_missing" and e.value.path == p


@pytest.mark.parametrize("stored", ["v9:0123", "forged", "", "V1:" + "0" * 64])
def test_malformed_approved_hash_on_a_generated_file_counts_as_missing(tmp_path, stored):
    # G-C (a): an unknown prefix is only "unverified" for files the current tooling did not write.
    p = approved_by_hand(tmp_path, generated_hash="v1:" + "0" * 64)
    from harness.yamlio import load_yaml

    raw = load_yaml(p.read_text(encoding="utf-8"))
    raw["review"]["approved_hash"] = stored
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioLoadError) as e:
        load_set([tmp_path], include_drafts=False)
    assert e.value.code == "approved_hash_missing"


def test_hand_approval_without_new_fields_stays_a_warning(tmp_path):
    approved_by_hand(tmp_path)
    ss = load_set([tmp_path], include_drafts=False)
    assert len(ss.scenarios) == 1 and len(ss.warnings) == 1 and "approval_unverified" in ss.warnings[0]


def as_human(text: str) -> str:
    return text.replace("author: llm", "author: human")


def test_llm_approval_without_hash_is_an_error(tmp_path):
    p = write(tmp_path, with_status("approved", "a"), "a.yaml")
    for drafts in (False, True):
        with pytest.raises(ScenarioLoadError) as e:
            load_set([tmp_path], include_drafts=drafts)
        assert e.value.code == "llm_approval_unhashed" and e.value.path == p


def test_llm_approval_with_unknown_hash_version_is_an_error(tmp_path):
    text = with_status("approved", "a").replace('notes: ""', 'notes: ""\n  approved_hash: "v9:0123"')
    write(tmp_path, text, "a.yaml")
    with pytest.raises(ScenarioLoadError) as e:
        load_set([tmp_path], include_drafts=False)
    assert e.value.code == "llm_approval_unhashed"


def test_human_approval_without_hash_stays_a_warning(tmp_path):
    write(tmp_path, as_human(with_status("approved", "a")), "a.yaml")
    ss = load_set([tmp_path], include_drafts=False)
    assert len(ss.scenarios) == 1 and len(ss.warnings) == 1 and "approval_unverified" in ss.warnings[0]


def test_llm_approval_with_a_valid_hash_is_unaffected(tmp_path):
    approved_with_hash(tmp_path)
    ss = load_set([tmp_path], include_drafts=False)
    assert ss.warnings == [] and [s.id for s in ss.scenarios] == ["a"]


def test_copying_generated_hash_into_approved_hash_does_not_verify(tmp_path):
    from harness.scenario.labels import content_hash
    from harness.yamlio import load_yaml

    p = approved_with_hash(tmp_path)
    raw = load_yaml(p.read_text(encoding="utf-8"))
    generated = content_hash(raw, "generated")  # what generation would have recorded for this very content
    raw["provenance"]["generated_hash"] = generated
    raw["review"]["approved_hash"] = generated  # copied by hand
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioLoadError) as e:
        load_set([tmp_path], include_drafts=False)
    assert e.value.code == "approved_content_changed"
