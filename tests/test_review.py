"""Tests for the review commands: list, show, edit (label protection) and set."""

import io
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml
from rich.console import Console

from harness.config import EvalConfig
from harness.generate.config import GenConfig
from harness.generate.pipeline import save_draft
from harness.review import blocking, edit_draft, find_draft, list_drafts, render_show, set_status
from harness.scenario.load import load_scenario
from harness.scenario.model import Evidence, Scenario
from tests.factories import T, ev, fact, probe, scenario
from tests.test_cli import too_easy
from tests.test_gen_checks import leak_scenario
from tests.test_selfcheck import truth_change

CFG = EvalConfig()
GEN = GenConfig(
    provider="fake",
    model="m",
    prompt_version="gen-test",
    license_allowlist=["MIT"],
    banned_cue_words=["outdated", "no longer", "rumor", "deprecated"],
)


def draft(tmp_path: Path, s: Scenario | None = None, name: str = "d.yaml") -> Path:
    s = s or truth_change()
    path = tmp_path / name
    save_draft(s, path)
    return path


def editor(tmp_path: Path, body: str, exit_code: int = 0) -> str:
    """An EDITOR command: a python script that receives the file path as `path`."""
    script = tmp_path / "editor.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\npath = Path(sys.argv[1])\ntext = path.read_text(encoding='utf-8')\n"
        + textwrap.dedent(body)
        + f"\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return shlex.join([sys.executable, str(script)])


def test_edit_text_saves_and_marks_edited(tmp_path):
    path = draft(tmp_path)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))")
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "saved"
    assert result.changed_fields == []
    assert [i for i in result.issues if i.level == "error"] == []
    saved = load_scenario(path)
    assert saved.provenance.reviewer_edited is True
    assert next(e for e in saved.events if e.id == "tc_n").text == "vendor v2 is the requirement"


def test_edit_saved_result_carries_check_issues(tmp_path):
    path = draft(tmp_path)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'nothing useful here'))")
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "saved"
    assert [i.code for i in result.issues if i.level == "error"] == ["marker_not_in_body"]


def test_edit_without_changes_is_unchanged(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, "pass"), CFG, GEN)
    assert result.status == "unchanged"
    assert path.read_bytes() == before
    assert load_scenario(path).provenance.reviewer_edited is False


def test_edit_that_only_reformats_is_unchanged(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, "path.write_text('# a comment\\n' + text)"), CFG, GEN)
    assert result.status == "unchanged"
    assert path.read_bytes() == before


def test_edit_label_change_reverted(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    cmd = editor(tmp_path, "path.write_text(text.replace('valid: true', 'valid: false', 1))")
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "reverted"
    assert result.changed_fields == ["facts.f_old.valid"]
    assert path.read_bytes() == before


def test_reverted_edit_is_kept_in_the_cache(tmp_path, monkeypatch):
    import re

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = draft(tmp_path)
    before = path.read_bytes()
    body = "path.write_text(text.replace('valid: true', 'valid: false', 1).replace('vendor v2 is required now', 'my careful rewrite'))"
    result = edit_draft(path, editor(tmp_path, body), CFG, GEN)
    assert result.status == "reverted"
    assert path.read_bytes() == before
    kept = result.kept_copy
    assert kept is not None and kept.parent == tmp_path / "cache" / "review"
    assert re.fullmatch(r"tc-\d{8}-\d{6}\.yaml", kept.name)
    text = kept.read_text(encoding="utf-8")
    assert "my careful rewrite" in text and "valid: false" in text


def test_reverted_edit_without_changes_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = draft(tmp_path)
    result = edit_draft(path, editor(tmp_path, "pass", exit_code=1), CFG, GEN)
    assert result.status == "reverted" and result.kept_copy is None
    assert not (tmp_path / "cache" / "review").exists()


def test_edit_swapping_same_time_events_reverted(tmp_path):
    s = scenario(
        {"f": fact(text="use tool", markers=["tool"])},
        [ev("e1", T("10-01 10:00"), fact="f", text="use the tool"), ev("e2", T("10-01 10:00"), fact="f", text="tool again")],
        [probe("p", "promote", T("10-02 10:00"), ["f"])],
    )
    path = draft(tmp_path, s)
    before = path.read_bytes()
    body = """
        data = __import__('yaml').safe_load(text)
        data['events'].reverse()
        path.write_text(__import__('yaml').safe_dump(data, sort_keys=False))
    """
    result = edit_draft(path, editor(tmp_path, body), CFG, GEN)
    assert result.status == "reverted"
    assert "event_order" in result.changed_fields
    assert path.read_bytes() == before


def test_edit_review_status_reverted(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, "path.write_text(text.replace('status: draft', 'status: approved'))"), CFG, GEN)
    assert result.status == "reverted"
    assert "review set" in result.reason
    assert path.read_bytes() == before


def test_edit_notes_is_allowed(tmp_path):
    path = draft(tmp_path)
    result = edit_draft(path, editor(tmp_path, "path.write_text(text.replace('notes: \\'\\'', 'notes: checked'))"), CFG, GEN)
    assert result.status == "saved"
    assert load_scenario(path).review.notes == "checked"


def test_edit_broken_yaml_reverted(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, "path.write_text('facts: [unclosed')"), CFG, GEN)
    assert result.status == "reverted"
    assert result.reason
    assert path.read_bytes() == before


def test_edit_schema_error_reverted(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, "path.write_text(text + 'bogus_key: 1\\n')"), CFG, GEN)
    assert result.status == "reverted"
    assert path.read_bytes() == before


def test_edit_editor_nonzero_reverted(tmp_path):
    path = draft(tmp_path)
    before = path.read_bytes()
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'changed'))", exit_code=3)
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "reverted"
    assert "3" in result.reason
    assert path.read_bytes() == before


def test_edit_missing_editor_reverted(tmp_path):
    path = draft(tmp_path)
    result = edit_draft(path, "definitely-not-an-editor-xyz", CFG, GEN)
    assert result.status == "reverted"
    assert "definitely-not-an-editor-xyz" in result.reason


def test_set_approved_blocked_by_issues(tmp_path):
    s = truth_change()
    events = [e.model_copy(update={"text": "nothing useful here"}) if e.id == "tc_o1" else e for e in s.events]
    path = draft(tmp_path, s.model_copy(update={"events": events}))
    before = path.read_bytes()
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues if i.level == "error"] == ["marker_not_in_body"]
    assert blocking(issues)
    assert path.read_bytes() == before
    assert load_scenario(path).review.status == "draft"


def test_set_approved_blocked_by_too_easy(tmp_path):
    path = draft(tmp_path, too_easy())
    before = path.read_bytes()
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["too_easy"]
    assert blocking(issues)
    assert path.read_bytes() == before


def test_set_approved_when_clean(tmp_path):
    path = draft(tmp_path)
    issues = set_status(path, "approved", "kim", "looks right", CFG, GEN)
    assert not blocking(issues)
    review = load_scenario(path).review
    assert (review.status, review.reviewer, review.notes) == ("approved", "kim", "looks right")


def test_set_approved_keeps_existing_notes_without_note(tmp_path):
    s = truth_change()
    s.review.notes = "earlier note"
    path = draft(tmp_path, s)
    set_status(path, "approved", "kim", None, CFG, GEN)
    assert load_scenario(path).review.notes == "earlier note"


def test_set_rejected_always(tmp_path):
    path = draft(tmp_path, too_easy())
    assert set_status(path, "rejected", "kim", "labels are wrong", CFG, GEN) == []
    review = load_scenario(path).review
    assert (review.status, review.reviewer, review.notes) == ("rejected", "kim", "labels are wrong")


def test_saved_yaml_keeps_key_order_and_iso_times(tmp_path):
    path = draft(tmp_path)
    set_status(path, "rejected", "kim", None, CFG, GEN)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(data)[:4] == ["id", "family", "mode", "language"]
    assert "+09:00" in path.read_text(encoding="utf-8")


def test_list_and_find_drafts(tmp_path):
    a = draft(tmp_path, truth_change("tc-a"), "tc-a.yaml")
    sub = tmp_path / "truth_change"
    sub.mkdir()
    b = draft(sub, truth_change("tc-b"), "tc-b.yaml")
    set_status(b, "rejected", "kim", None, CFG, GEN)
    (tmp_path / "broken.yaml").write_text("facts: [unclosed", encoding="utf-8")
    rows = {r.id: r for r in list_drafts(tmp_path)}
    assert set(rows) == {"tc-a", "tc-b", "broken"}
    assert (rows["tc-a"].status, rows["tc-a"].family, rows["tc-a"].path) == ("draft", "truth_change", a)
    assert rows["tc-b"].status == "rejected"
    assert rows["broken"].status == "unreadable" and rows["broken"].warnings
    assert find_draft(tmp_path, "tc-b") == [b]
    assert find_draft(tmp_path, "nope") == []


def test_list_drafts_reports_generated_at_and_warnings(tmp_path):
    s = truth_change("tc-w")
    s.facts["f_new"].markers = []
    path = draft(tmp_path, s, "tc-w.yaml")
    (row,) = list_drafts(path.parent)
    assert row.generated_at is None
    assert row.warnings == ["markers_missing"]


def render(s: Scenario, **kw) -> str:
    console = Console(record=True, width=200, file=io.StringIO())
    console.print(render_show(s, CFG, **kw))
    return console.export_text()


def test_show_renders_labels_and_baselines():
    text = render(truth_change())
    for word in ("valid_until", "valid_from", "oracle", "all_store", "f_old", "f_new", "vendor v1", "p_block", "p_promote"):
        assert word in text
    assert "all_store_noacl" not in text


def test_show_timeline_and_evidence():
    s = truth_change()
    s.provenance.evidence = [Evidence(event_id="tc_n", trace_id="traj-1", message_index=4, quote="vendor v2 is required", origin="quoted")]
    s.provenance.fabricated_facts = ["f_old"]
    s.provenance.llm_model = "claude-x"
    s.provenance.provider = "anthropic"
    text = render(s)
    assert "quoted" in text and "vendor v2 is required" in text
    assert "claude-x" in text and "anthropic" in text
    assert "tc_o1" in text and "tc_n" in text
    assert text.index("tc_o1") < text.index("tc_n") < text.index("p_block", text.index("tc_n"))
    assert text.count("fabricated") == 1


def test_show_leak_family_adds_noacl_baseline():
    text = render(load_scenario(Path("scenarios/examples/leak_cross_team_config.yaml")))
    assert "all_store_noacl" in text and "leaks 2" in text


def test_show_invalid_scenario_lists_issues_not_baselines():
    s = truth_change()
    bad = s.model_copy(update={"events": [s.events[0].model_copy(update={"fact": "nope"})]})
    text = render(bad)
    assert "unknown_fact" in text


def test_review_works_without_gen_deps():
    code = (
        "import sys\n"
        "for n in ('anthropic', 'openai', 'pyarrow', 'huggingface_hub'):\n"
        "    sys.modules[n] = None\n"
        "import harness.review\n"
        "import harness.cli\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_edit_of_approved_resets_to_draft(tmp_path):
    path = draft(tmp_path)
    set_status(path, "approved", "kim", "fine", CFG, GEN)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'nothing useful here'))")
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "saved" and result.reset_to_draft is True
    assert blocking(result.issues)
    review = load_scenario(path).review
    assert (review.status, review.reviewer, review.notes) == ("draft", None, "fine")
    # the gate holds: it cannot be approved again while the issue stands
    assert blocking(set_status(path, "approved", "kim", None, CFG, GEN))
    assert load_scenario(path).review.status == "draft"


def test_edit_of_rejected_resets_to_draft(tmp_path):
    path = draft(tmp_path)
    set_status(path, "rejected", "kim", "unsure", CFG, GEN)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))")
    result = edit_draft(path, cmd, CFG, GEN)
    assert result.status == "saved" and result.reset_to_draft is True
    review = load_scenario(path).review
    assert (review.status, review.reviewer, review.notes) == ("draft", None, "unsure")


def test_edit_of_draft_does_not_report_reset(tmp_path):
    path = draft(tmp_path)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))")
    assert edit_draft(path, cmd, CFG, GEN).reset_to_draft is False


def test_unchanged_edit_keeps_approval(tmp_path):
    path = draft(tmp_path)
    set_status(path, "approved", "kim", None, CFG, GEN)
    assert edit_draft(path, editor(tmp_path, "pass"), CFG, GEN).status == "unchanged"
    assert load_scenario(path).review.status == "approved"


def test_cli_import_is_lazy():
    code = (
        "import sys\n"
        "import harness.cli\n"
        "bad = [m for m in ('harness.review', 'harness.generate.pipeline') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_rich_is_a_declared_dependency():
    import re
    import tomllib

    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    names = {re.split(r"[<>=!~ \[;]", d, maxsplit=1)[0].lower() for d in project["project"]["dependencies"]}
    assert "rich" in names  # cli.py and review.py import it directly


# ---- secret markers are not editable ------------------------------------------------------------

CLEAR_SECRETS = """
    data = __import__('yaml').safe_load(text)
    for e in data['events']:
        e['secret_markers'] = []
    path.write_text(__import__('yaml').safe_dump(data, sort_keys=False))
"""


def test_review_edit_reverts_secret_marker_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = draft(tmp_path, leak_scenario())
    before = path.read_bytes()
    result = edit_draft(path, editor(tmp_path, CLEAR_SECRETS), CFG, GEN)
    assert result.status == "reverted"
    assert result.changed_fields == ["events.sec.secret_markers"]
    assert path.read_bytes() == before
    assert load_scenario(path).events[0].secret_markers == ["sk-live-4242"]
    kept = result.kept_copy  # the reviewer's edited text is kept, as for any label edit
    assert kept is not None and kept.parent == tmp_path / "cache" / "review"
    assert load_scenario(kept).events[0].secret_markers == []


def test_review_edit_cannot_swap_secret_markers_for_junk(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = draft(tmp_path, leak_scenario())
    before = path.read_bytes()
    junk = "path.write_text(text.replace('- sk-live-4242', '- zzz'))"
    result = edit_draft(path, editor(tmp_path, junk), CFG, GEN)
    assert result.status == "reverted" and result.changed_fields == ["events.sec.secret_markers"]
    assert path.read_bytes() == before
    # Even a consistent swap (body and marker both renamed) is a label change.
    both = "path.write_text(text.replace('sk-live-4242', 'zzz'))"
    result = edit_draft(path, editor(tmp_path, both), CFG, GEN)
    assert result.status == "reverted" and "events.sec.secret_markers" in result.changed_fields
    assert path.read_bytes() == before


def test_review_edit_cannot_change_the_recorded_secret_events(tmp_path, monkeypatch):
    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    path = draft(tmp_path, leak_scenario())
    before = path.read_bytes()
    body = """
        data = __import__('yaml').safe_load(text)
        data['provenance']['secret_events'] = ['pub']
        path.write_text(__import__('yaml').safe_dump(data, sort_keys=False))
    """
    result = edit_draft(path, editor(tmp_path, body), CFG, GEN)
    assert result.status == "reverted" and result.changed_fields == ["provenance.secret_events"]
    assert path.read_bytes() == before


def test_review_edit_of_text_still_checks_the_recorded_secret_events(tmp_path):
    path = draft(tmp_path, leak_scenario())
    body = "path.write_text(text.replace('release pipeline runs nightly', 'release pipeline uses sk-live-4242'))"
    result = edit_draft(path, editor(tmp_path, body), CFG, GEN)
    assert result.status == "saved"
    assert "secret_marker_not_exclusive" in [i.code for i in result.issues]


def test_set_approved_of_an_old_file_without_secret_events_warns_but_approves(tmp_path):
    import yaml

    path = draft(tmp_path, leak_scenario())
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["provenance"]["secret_events"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    issues = set_status(path, "approved", "me", None, CFG, GEN)
    assert [i.code for i in issues] == ["secret_events_not_recorded", "label_fingerprint_not_recorded"]
    assert load_scenario(path).review.status == "approved"


def old_file(tmp_path):
    import yaml

    path = draft(tmp_path, leak_scenario(recorded=False))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["provenance"]["secret_events"] is None  # saved as null, not as a recorded []
    return path


def test_old_file_stays_unrecorded_after_review_set_and_edit(tmp_path):
    """The first harness write must not change an old file's meaning (no `no_secret_events` afterwards)."""
    from harness.generate.checks import check_draft

    path = old_file(tmp_path)
    set_status(path, "approved", "me", None, CFG, GEN)
    after = load_scenario(path)
    assert after.review.status == "approved" and after.provenance.secret_events is None
    findings = check_draft(after, CFG, GEN)
    assert [i.code for i in findings if i.level == "error"] == []
    assert "secret_events_not_recorded" in [i.code for i in findings]

    body = "path.write_text(text.replace('release pipeline runs nightly', 'release pipeline runs hourly'))"
    result = edit_draft(path, editor(tmp_path, body), CFG, GEN)
    assert result.status == "saved" and result.changed_fields == []  # None -> None is not a label change
    assert [i.code for i in result.issues if i.level == "error"] == []
    saved = load_scenario(path)
    assert saved.provenance.secret_events is None and saved.provenance.reviewer_edited
    assert set_status(path, "approved", "me", None, CFG, GEN)[0].code == "secret_events_not_recorded"


def test_set_approved_of_a_secret_event_without_markers_is_blocked(tmp_path):
    s = leak_scenario()
    s.events[0].secret_markers = []
    path = draft(tmp_path, s)
    issues = set_status(path, "approved", "me", None, CFG, GEN)
    assert "secret_missing" in [i.code for i in blocking(issues)]
    assert load_scenario(path).review.status == "draft"


def test_show_lists_the_secret_findings(tmp_path):
    s = leak_scenario()
    s.events[0].secret_markers = []
    out = io.StringIO()
    Console(file=out, width=200, force_terminal=False).print(render_show(s, CFG, GEN))
    assert "secret_missing" in out.getvalue()


# ---- approval integrity ---------------------------------------------------------------------------


def generated(tmp_path: Path, s: Scenario | None = None, name: str = "g.yaml") -> Path:
    """A draft saved the way `generate` saves it: label fingerprint and generated hash recorded."""
    from harness.generate.pipeline import mark_generated

    s = s or truth_change()
    mark_generated(s)
    path = tmp_path / name
    save_draft(s, path)
    return path


def _edit_yaml(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_direct_valid_until_edit_blocks_approval(tmp_path):
    """Reproduces d4 (a): a label changed in an editor, outside `review edit`, cannot be approved."""
    from typer.testing import CliRunner

    from harness.cli import app

    path = generated(tmp_path, truth_change("tc-x"))

    def move(data):
        data["facts"]["f_old"]["valid_until"] = "2026-10-09T08:00:00+09:00"

    _edit_yaml(path, move)
    before = path.read_bytes()
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in blocking(issues)] == ["labels_changed_outside_review"]
    assert path.read_bytes() == before and load_scenario(path).review.status == "draft"

    result = CliRunner().invoke(app, ["review", "set", "tc-x", "approved", "--reviewer", "kim", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "labels_changed_outside_review" in result.output and "outside the review tool" in result.output
    assert path.read_bytes() == before
    # rejecting it still works
    assert set_status(path, "rejected", "kim", "labels moved", CFG, GEN) == []


def test_text_edit_outside_review_edit_can_still_be_approved(tmp_path):
    path = generated(tmp_path)

    def reword(data):
        next(e for e in data["events"] if e["id"] == "tc_n")["text"] = "vendor v2 is the requirement"

    _edit_yaml(path, reword)
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert not blocking(issues) and not [i for i in issues if i.code.startswith("label")]
    assert load_scenario(path).review.status == "approved"


def test_set_approved_records_the_content_hash(tmp_path):
    from harness.scenario.labels import content_hash
    from harness.scenario.load import load_set
    from harness.yamlio import load_yaml

    path = generated(tmp_path)
    set_status(path, "approved", "kim", None, CFG, GEN)
    raw = load_yaml(path.read_text(encoding="utf-8"))
    assert raw["review"]["approved_hash"] == content_hash(raw, "approval")
    assert load_set([tmp_path], include_drafts=False).warnings == []
    set_status(path, "rejected", "kim", None, CFG, GEN)
    assert load_scenario(path).review.approved_hash is None


def test_set_approved_without_a_recorded_fingerprint_warns_but_approves(tmp_path):
    path = draft(tmp_path)  # saved without the generation hashes, like an older file
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["label_fingerprint_not_recorded"] and not blocking(issues)
    assert load_scenario(path).review.status == "approved"


def test_set_approved_with_an_unprefixed_fingerprint_warns_but_approves(tmp_path):
    """An older fingerprint (no `l1:` prefix) is unverifiable, not a mismatch."""
    path = generated(tmp_path)
    old = load_scenario(path).provenance.label_fingerprint
    assert old is not None and old.startswith("l1:")
    _edit_yaml(path, lambda d: d["provenance"].update(label_fingerprint=old[3:]))
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["label_fingerprint_not_recorded"] and not blocking(issues)
    assert load_scenario(path).review.status == "approved"


def test_set_approved_of_an_llm_draft_whose_fingerprint_was_removed_is_refused(tmp_path):
    # G-C (b): `generate` always records both hashes for an LLM draft, so a missing fingerprint
    # next to a generated hash was removed by hand.
    s = truth_change()
    s.provenance.author = "llm"
    path = generated(tmp_path, s)
    _edit_yaml(path, lambda d: d["provenance"].pop("label_fingerprint"))
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["labels_changed_outside_review"]
    assert load_scenario(path).review.status == "draft"


def test_set_approved_of_a_non_llm_skeleton_without_fingerprint_still_warns(tmp_path):
    from harness.generate.pipeline import mark_generated

    s = truth_change()
    s.provenance.author = "human"
    mark_generated(s, fingerprint=False)  # a `scenario new` skeleton filled by hand
    path = tmp_path / "h.yaml"
    save_draft(s, path)
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["label_fingerprint_not_recorded"] and not blocking(issues)


def test_set_approved_with_a_prefixed_wrong_fingerprint_is_refused(tmp_path):
    path = generated(tmp_path)
    _edit_yaml(path, lambda d: d["provenance"].update(label_fingerprint="l1:" + "0" * 64))
    issues = set_status(path, "approved", "kim", None, CFG, GEN)
    assert [i.code for i in issues] == ["labels_changed_outside_review"]
    assert load_scenario(path).review.status == "draft"


def test_review_edit_keeps_the_harness_written_hashes(tmp_path):
    path = generated(tmp_path)
    s = load_scenario(path)
    body = (
        "text = text.replace('vendor v2 is required now', 'vendor v2 is the requirement')\n"
        f"text = text.replace({s.provenance.generated_hash!r}, 'v1:forged')\n"
        f"text = text.replace({s.provenance.label_fingerprint!r}, 'forged')\n"
        "path.write_text(text)"
    )
    assert edit_draft(path, editor(tmp_path, body), CFG, GEN).status == "saved"
    after = load_scenario(path)
    assert after.provenance.generated_hash == s.provenance.generated_hash
    assert after.provenance.label_fingerprint == s.provenance.label_fingerprint


def test_review_edit_of_approved_then_reapproval_verifies(tmp_path):
    from harness.scenario.load import load_set

    path = generated(tmp_path)
    set_status(path, "approved", "kim", None, CFG, GEN)
    cmd = editor(tmp_path, "path.write_text(text.replace('vendor v2 is required now', 'vendor v2 is the requirement'))")
    assert edit_draft(path, cmd, CFG, GEN).reset_to_draft is True
    assert load_scenario(path).review.approved_hash is None
    assert not blocking(set_status(path, "approved", "kim", None, CFG, GEN))
    assert load_set([tmp_path], include_drafts=False).warnings == []


def test_keep_edit_stays_inside_cache_for_odd_ids(tmp_path, monkeypatch):
    """Reproduces d11: an id with path separators or `..` cannot place the kept copy elsewhere."""
    from harness.review import _keep_edit

    monkeypatch.setenv("T5_HARNESS_CACHE", str(tmp_path / "cache"))
    folder = (tmp_path / "cache" / "review").resolve()
    for odd in ("../../escaped-by-id", "a/b/c", "/abs/path", "..", "x\\..\\y", "id with spaces"):
        kept = _keep_edit(odd, b"text")
        assert kept.resolve().parent == folder, odd
        assert kept.read_bytes() == b"text"
    assert not list(tmp_path.glob("escaped-by-id*"))
    names = sorted(p.name for p in folder.iterdir())
    assert all("/" not in n and "\\" not in n and " " not in n for n in names)
    assert any(n.startswith(".._.._escaped-by-id-") for n in names)


def test_list_flags_an_approval_that_does_not_verify(tmp_path):
    path = generated(tmp_path)
    set_status(path, "approved", "kim", None, CFG, GEN)
    assert "approved_content_changed" not in list_drafts(tmp_path)[0].warnings

    def reword(data):
        data["events"][0]["text"] += " edited after approval"

    _edit_yaml(path, reword)
    assert list_drafts(tmp_path)[0].warnings[0] == "approved_content_changed"


def _with_nel(s: Scenario) -> Scenario:
    """U+0085 (NEL) is written raw by PyYAML with allow_unicode and read back as a space."""
    e = s.events[0]
    s.events[0] = e.model_copy(update={"text": e.text + " a\x85b"})
    return s


def test_hashes_are_taken_from_the_text_that_is_written(tmp_path):
    # G-E (repro fuzz.py): hashing model_dump() missed a YAML round-trip change.
    from harness.scenario.labels import content_hash
    from harness.scenario.load import load_set
    from harness.yamlio import load_yaml

    path = generated(tmp_path, _with_nel(truth_change()))
    raw = load_yaml(path.read_text(encoding="utf-8"))
    assert raw["provenance"]["generated_hash"] == content_hash(raw, "generated")
    assert not blocking(set_status(path, "approved", "kim", None, CFG, GEN))
    raw = load_yaml(path.read_text(encoding="utf-8"))
    assert raw["review"]["approved_hash"] == content_hash(raw, "approval")
    ss = load_set([tmp_path], include_drafts=False)
    assert [x.id for x in ss.scenarios] == [truth_change().id] and ss.warnings == []
