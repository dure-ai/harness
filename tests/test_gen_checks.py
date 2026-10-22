from harness.config import EvalConfig
from harness.generate.checks import _secret_issues, check_draft, secret_events
from harness.generate.config import GenConfig
from harness.scenario.model import Evidence, Provenance, Scenario
from harness.scenario.validate import Issue
from tests.factories import T, ev, fact, probe, scenario
from tests.test_cli import too_easy
from tests.test_selfcheck import poison, truth_change

CFG = EvalConfig()
GEN = GenConfig(
    provider="fake",
    model="m",
    prompt_version="gen-test",
    license_allowlist=["MIT"],
    banned_cue_words=["outdated", "no longer", "rumor", "deprecated"],
)


def with_event_text(s: Scenario, event_id: str, text: str) -> Scenario:
    events = [e.model_copy(update={"text": text}) if e.id == event_id else e for e in s.events]
    return s.model_copy(update={"events": events})


def codes(issues: list[Issue]) -> list[str]:
    return [i.code for i in issues]


def only(issues: list[Issue], code: str) -> Issue:
    hits = [i for i in issues if i.code == code]
    assert len(hits) == 1, issues
    return hits[0]


def test_clean_draft_has_no_issues():
    assert check_draft(truth_change(), CFG, GEN) == []


def test_invalid_scenario_stops_before_other_checks():
    s = truth_change()
    bad = s.model_copy(update={"events": [s.events[0].model_copy(update={"fact": "nope"})]})
    issues = check_draft(bad, CFG, GEN)
    assert any(i.level == "error" for i in issues)
    assert {"marker_not_in_body", "other_fact_marker", "cue_word"}.isdisjoint(codes(issues))
    assert "too_easy" not in codes(issues)


def test_marker_not_in_body():
    s = with_event_text(truth_change(), "tc_o1", "the build setup works fine")
    issue = only(check_draft(s, CFG, GEN), "marker_not_in_body")
    assert issue.level == "error"
    assert "tc_o1" in issue.message


def test_marker_match_ignores_case():
    s = with_event_text(truth_change(), "tc_o1", "VENDOR V1 is what the build uses")
    assert check_draft(s, CFG, GEN) == []


def with_aux_fact(s: Scenario) -> Scenario:
    """Add a fact no probe asks about, so its marker is only the generator layer's concern.

    The core validator's `marker_bleed` fires for facts a probe asks about; a marker of a fact
    outside every probe's `about` reaches only `other_fact_marker`.
    """
    aux = fact(text="use vendor v3", markers=["vendor v3"])
    return s.model_copy(update={"facts": {**s.facts, "f_aux": aux}})


def test_other_fact_marker():
    s = with_aux_fact(truth_change())
    s = with_event_text(s, "tc_o1", "vendor v1 works, though vendor v3 is coming")
    issues = check_draft(s, CFG, GEN)
    issue = only(issues, "other_fact_marker")
    assert issue.level == "error"
    assert "tc_o1" in issue.message and "f_aux" in issue.message
    assert "marker_bleed" not in codes(issues)


def test_other_fact_marker_in_noise_event():
    s = with_aux_fact(truth_change())
    noise = ev("noise", T("10-05 10:00"), text="we also discussed vendor v3 today")
    s = s.model_copy(update={"events": [*s.events, noise]})
    issues = check_draft(s, CFG, GEN)
    issue = only(issues, "other_fact_marker")
    assert "noise" in issue.message
    assert "marker_bleed" not in codes(issues)


def test_cue_word_flagged():
    s = with_event_text(truth_change(), "tc_o1", "vendor v1 is outdated but the build still uses it")
    issue = only(check_draft(s, CFG, GEN), "cue_word")
    assert issue.level == "error"
    assert "tc_o1" in issue.message and "outdated" in issue.message


def test_cue_word_phrase_and_probe_query():
    s = with_event_text(truth_change(), "tc_o2", "vendor v1 is No   Longer what the build needs")
    probes = [
        p.model_copy(update={"query": "is this rumor true? vendor v2"}) if p.id == "p_promote" else p
        for p in s.probes
    ]
    s = s.model_copy(update={"probes": probes})
    issues = [i for i in check_draft(s, CFG, GEN) if i.code == "cue_word"]
    assert len(issues) == 2
    assert any("tc_o2" in i.message for i in issues)
    assert any("p_promote" in i.message for i in issues)


def test_cue_word_needs_word_boundary():
    s = with_event_text(truth_change(), "tc_o1", "vendor v1 handles the outdatedness check")
    assert "cue_word" not in codes(check_draft(s, CFG, GEN))


def deprecated_flag_scenario(text: str) -> Scenario:
    facts = {
        "dep": fact(text="the --legacy flag is deprecated", markers=["--legacy flag is deprecated"]),
        "other": fact(text="use --modern", markers=["use --modern"]),
    }
    events = [
        ev("e1", T("10-01 10:00"), fact="dep", text=text),
        ev("e2", T("10-02 10:00"), fact="other", text="please use --modern for new jobs"),
    ]
    probes = [probe("p1", "promote", T("10-05 10:00"), ["dep"], query="legacy flag")]
    return scenario(facts, events, probes, family="normal_repeat")


def test_cue_word_allowed_when_part_of_marker():
    s = deprecated_flag_scenario("note: the --legacy flag is deprecated in the cli")
    assert check_draft(s, CFG, GEN) == []


def test_cue_word_not_allowed_for_other_event_fact():
    # "deprecated" is in fact dep's marker, but e2 states a different fact.
    s = deprecated_flag_scenario("note: the --legacy flag is deprecated in the cli")
    s = with_event_text(s, "e2", "use --modern, the old way is deprecated")
    issue = only(check_draft(s, CFG, GEN), "cue_word")
    assert "e2" in issue.message


def leak_scenario(*, secret_in_secret=True, secret_in_public=False, secret_in_visible=False) -> Scenario:
    """A leak scenario; the secret can be misplaced where only the generator layer sees it.

    `secret_in_public` puts the secret into a second event of the secret's own owner, which no
    asker can see (the generator layer flags it; the core validator does not). `secret_in_visible`
    puts it into an event an asker sees, which the core validator catches first.
    """
    facts = {"f": fact(text="deploy via the release pipeline", markers=["release pipeline"])}
    secret_text = "release pipeline token is sk-live-4242" if secret_in_secret else "release pipeline notes"
    public_text = "release pipeline uses sk-live-4242" if secret_in_visible else "release pipeline runs nightly"
    events = [
        ev("sec", T("10-01 10:00"), fact="f", user="u1", scope="personal", text=secret_text,
           secret_markers=["sk-live-4242"]),
        ev("pub", T("10-02 10:00"), fact="f", user="u2", text=public_text),
    ]
    if secret_in_public:
        events.append(ev("side", T("10-03 10:00"), fact="f", user="u1", scope="personal",
                         text="release pipeline copy: sk-live-4242"))
    probes = [
        probe("pl", "leak", T("10-05 10:00"), ["f"], user="u3", query="release pipeline"),
        probe("pp", "promote", T("10-05 10:00"), ["f"], user="u2", query="release pipeline"),
    ]
    return scenario(facts, events, probes, family="leak_personal")


def test_secret_events_lists_events_with_secret_markers_in_file_order():
    s = leak_scenario()
    assert secret_events(s) == ["sec"]
    assert secret_events(truth_change()) == []


def test_secret_placement():
    s = leak_scenario()
    assert check_draft(s, CFG, GEN, secret_events(s)) == []


def test_secret_not_in_secret_event():
    s = leak_scenario(secret_in_secret=False)
    issues = check_draft(s, CFG, GEN, ["sec"])
    issue = only(issues, "secret_not_in_secret_event")
    assert issue.level == "error"
    assert "sec" in issue.message


def test_secret_in_public_event():
    s = leak_scenario(secret_in_public=True)
    issues = check_draft(s, CFG, GEN, ["sec"])
    issue = only(issues, "secret_in_public_event")
    assert issue.level == "error"
    assert "side" in issue.message
    assert "secret_marker_not_exclusive" not in codes(issues)


def test_core_validator_code_wins_when_both_layers_fire():
    s = leak_scenario(secret_in_visible=True)
    # the generator layer alone would flag the visible event too
    assert codes(_secret_issues(s, ["sec"])) == ["secret_in_public_event"]
    issues = check_draft(s, CFG, GEN, ["sec"])
    assert "secret_marker_not_exclusive" in codes(issues)
    assert "secret_in_public_event" not in codes(issues)  # check_draft stops at validator errors


def test_secret_checks_skipped_without_secret_event_ids():
    s = leak_scenario(secret_in_public=True)
    assert "secret_in_public_event" not in codes(check_draft(s, CFG, GEN))


def test_discrimination_failure_reported():
    s = too_easy()
    issues = check_draft(s, CFG, GEN)
    assert any(i.code == "too_easy" for i in issues)


def test_discrimination_not_run_on_invalid_scenario():
    s = too_easy()
    bad = s.model_copy(update={"events": [s.events[0].model_copy(update={"fact": "nope"})]})
    issues = check_draft(bad, CFG, GEN)
    assert any(i.level == "error" for i in issues)
    assert "too_easy" not in codes(issues)


def with_evidence(s: Scenario, *entries: Evidence) -> Scenario:
    prov = Provenance(author="llm", evidence=list(entries))
    return s.model_copy(update={"provenance": prov})


def test_quoted_evidence_mismatch():
    s = with_evidence(
        truth_change(),
        Evidence(event_id="tc_o1", quote="pytest -x tests/unit", origin="quoted"),
    )
    issue = only(check_draft(s, CFG, GEN), "quoted_evidence_mismatch")
    assert issue.level == "error"
    assert "tc_o1" in issue.message


def test_quoted_evidence_match_folds_whitespace_and_case():
    s = with_event_text(truth_change(), "tc_o1", "vendor v1 works;\n  Build   Step is ok")
    s = with_evidence(
        s,
        Evidence(event_id="tc_o1", quote="build step IS ok", origin="quoted"),
        Evidence(event_id="tc_o2", quote="not in the text at all", origin="written"),
        Evidence(event_id="tc_n", origin="quoted"),
    )
    assert check_draft(s, CFG, GEN) == []
