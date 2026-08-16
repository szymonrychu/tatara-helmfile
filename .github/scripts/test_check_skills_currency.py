import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]
SCRIPT = HERE / "check_skills_currency.py"
_spec = importlib.util.spec_from_file_location("check_skills_currency", SCRIPT)
currency = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(currency)

LS_REMOTE = (
    "9f1c0e4\trefs/tags/v2.1.0\n"
    "1a2b3c4\trefs/tags/v2.1.1\n"
    "1a2b3c4\trefs/tags/v2.1.1^{}\n"
    "5d6e7f8\trefs/tags/v2.2.0\n"
    "aabbcc0\trefs/tags/v2.3.0\n"
    "ddeeff1\trefs/tags/v2.4.0\n"
    "0011223\trefs/heads/main\n"
)


# --- tag parsing ---


def test_parse_tags_is_semver_ordered_and_drops_peels_and_non_tags():
    assert currency.parse_tags(LS_REMOTE) == [
        "v2.1.0",
        "v2.1.1",
        "v2.2.0",
        "v2.3.0",
        "v2.4.0",
    ]


def test_parse_tags_orders_numerically_not_lexically():
    text = "a\trefs/tags/v2.9.0\nb\trefs/tags/v2.10.0\nc\trefs/tags/v10.0.0\n"
    assert currency.parse_tags(text) == ["v2.9.0", "v2.10.0", "v10.0.0"]


def test_parse_tags_ignores_non_semver_tags():
    text = "a\trefs/tags/v2.1.0\nb\trefs/tags/nightly\nc\trefs/tags/v2.1\n"
    assert currency.parse_tags(text) == ["v2.1.0"]


# --- the lag rule: 0 or 1 release behind is one deploy train, 2+ is drift ---


def test_current_pin_is_clean():
    tags = currency.parse_tags(LS_REMOTE)
    assert currency.evaluate({"project-tatara": "v2.4.0"}, tags) == []


def test_one_release_behind_is_clean():
    """A pin one tag behind is a deploy train in flight, not drift."""
    tags = currency.parse_tags(LS_REMOTE)
    assert currency.evaluate({"project-tatara": "v2.3.0"}, tags) == []


def test_two_releases_behind_is_reported():
    tags = currency.parse_tags(LS_REMOTE)
    problems = currency.evaluate({"project-tatara": "v2.2.0"}, tags)
    assert len(problems) == 1
    assert "project-tatara" in problems[0]
    assert "v2.2.0" in problems[0] and "v2.4.0" in problems[0]
    assert "2 published tags behind" in problems[0]


def test_the_live_state_this_check_exists_for():
    """#397: all three projects at v2.1.1 while skills is at v2.4.0."""
    tags = currency.parse_tags(LS_REMOTE)
    pins = {
        "project-tatara": "v2.1.1",
        "project-infrastructure": "v2.1.1",
        "project-mtg": "v2.1.1",
    }
    problems = currency.evaluate(pins, tags)
    assert len(problems) == 3
    assert all("3 published tags behind" in p for p in problems)


# --- fail-closed ---


def test_a_pin_absent_from_the_tag_list_is_reported():
    """A pin naming no published tag means the pin site or the remote moved."""
    tags = currency.parse_tags(LS_REMOTE)
    problems = currency.evaluate({"project-tatara": "v9.9.9"}, tags)
    assert len(problems) == 1
    assert "names no published tatara-agent-skills tag" in problems[0]


def test_no_tags_at_all_is_reported_rather_than_vacuously_clean():
    problems = currency.evaluate({"project-tatara": "v2.4.0"}, [])
    assert len(problems) == 1
    assert "no semver tags" in problems[0]


def test_an_unreadable_pin_site_is_reported_rather_than_dropped():
    """A file read_pins could not parse must not silently leave the fleet unchecked."""
    tags = currency.parse_tags(LS_REMOTE)
    problems = currency.evaluate({"project-tatara": None}, tags)
    assert len(problems) == 1
    assert "no single readable skillsRef" in problems[0]


def test_read_pins_maps_a_malformed_file_to_none(tmp_path):
    good = tmp_path / "values" / "project-good"
    bad = tmp_path / "values" / "project-bad"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "common.yaml").write_text("      skillsRef: v2.4.0\n")
    (bad / "common.yaml").write_text("      skillsRef: v2.4.0\n      skillsRef: v2.3.0\n")
    assert currency.read_pins(tmp_path) == {
        "project-good": "v2.4.0",
        "project-bad": None,
    }


def test_no_pins_at_all_is_reported():
    tags = currency.parse_tags(LS_REMOTE)
    problems = currency.evaluate({}, tags)
    assert len(problems) == 1
    assert "no values/project-*/common.yaml" in problems[0]


def test_fetch_failure_raises_rather_than_returning_an_empty_list():
    """A check that silently stops checking is worse than no check."""
    with pytest.raises(currency.FetchError):
        currency.fetch_tags("https://example.invalid/nope.git", runner=_failing_runner)


def _failing_runner(argv):
    raise OSError("network is unreachable")


def test_fetch_tags_returns_the_runner_output_verbatim():
    assert currency.fetch_tags("x", runner=lambda argv: LS_REMOTE) == LS_REMOTE


# --- against the real repo ---


def test_real_repo_pins_are_readable():
    pins = currency.read_pins(REPO_ROOT)
    assert set(pins) == {
        "project-tatara",
        "project-infrastructure",
        "project-mtg",
    }
    assert all(v.startswith("v") for v in pins.values())
