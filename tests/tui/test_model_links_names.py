"""Kiln links and run config names on the run screen (functional spec 7.8;
architecture section 8)."""

from model_events import HOST

from kiln_harness_tuner.tui.model.links import compare_url, lineage_url
from kiln_harness_tuner.tui.model.names import LOOKUP_INTERVAL_SECS, RcNames

BASE = f"{HOST}/specs/p1/t1/compare"


def test_compare_url_with_one_config_still_has_two_columns():
    assert compare_url(HOST, "p1", "t1", ["rc1"]) == (
        f"{BASE}?models=rc1&columns=2&from=optimize"
    )


def test_compare_url_drops_repeats_and_keeps_the_order():
    assert compare_url(HOST, "p1", "t1", ["rc1", "rc2", "rc1", "", "rc3"]) == (
        f"{BASE}?models=rc1,rc2,rc3&columns=3&from=optimize"
    )


def test_compare_url_keeps_the_first_ten():
    ids = [f"rc{n}" for n in range(11)]
    url = compare_url(HOST, "p1", "t1", ids)
    assert url == f"{BASE}?models={','.join(ids[:10])}&columns=10&from=optimize"


def test_lineage_url_with_and_without_a_selection():
    lineage = f"{HOST}/optimize/p1/t1/experiment_lineage"
    assert lineage_url(HOST, "p1", "t1") == lineage
    assert lineage_url(HOST, "p1", "t1", "rc 7") == f"{lineage}?selected=rc%207"


def test_rc_names_start_from_the_seed():
    names = RcNames({"rc0": "rc 0003"})
    assert names.name("rc0") == "rc 0003"
    names.see("rc0")
    assert not names.stale


def test_an_unknown_id_makes_the_names_stale_once():
    names = RcNames()
    names.see("rc1")
    assert names.stale and names.lookup_due(0.0)
    names.lookup_started(0.0)
    assert not names.lookup_due(100.0)  # one lookup at a time
    assert names.learn([{"id": "rc2", "name": "Other"}])
    # rc1 was not in the list: seeing it again asks nothing.
    names.see("rc1")
    assert not names.stale


def test_a_lookup_runs_at_most_every_ten_seconds():
    names = RcNames()
    names.see("rc1")
    names.lookup_started(100.0)
    names.lookup_failed()
    names.see("rc2")
    assert not names.lookup_due(100.0 + LOOKUP_INTERVAL_SECS - 1)
    assert names.lookup_due(100.0 + LOOKUP_INTERVAL_SECS)


def test_learning_names_and_descriptions():
    names = RcNames()
    names.see("rc1")
    names.lookup_started(0.0)
    rows = [
        {"id": "rc1", "name": "Hold fields wrapper", "description": "wraps it"},
        {"id": "rc2", "name": ""},
        {"name": "no id"},
    ]
    assert names.learn(rows)
    assert not names.learn(rows)
    assert (names.name("rc1"), names.description("rc1")) == (
        "Hold fields wrapper",
        "wraps it",
    )
    assert names.name("rc2") is None
    assert not names.stale


def test_labels_before_and_after_the_name_is_known():
    names = RcNames()
    assert names.label("rc1") == "rc1"
    assert names.label("rc1", "w3-4") == "w3-4"
    names.learn([{"id": "rc1", "name": "Hold fields wrapper"}])
    assert names.label("rc1") == "Hold fields wrapper"
    assert names.label("rc1", "w3-4") == "Hold fields wrapper · w3-4"
