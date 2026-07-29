import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]
SCRIPT = HERE / "check_pin_coverage.py"
_spec = importlib.util.spec_from_file_location("check_pin_coverage", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def facts(wrapper=None, skills=None, releases=None, operator_tag="v1.35.1"):
    """A clean three-project fleet; each kwarg overrides one axis."""
    return {
        "releases": {
            "tatara-chat": "0.1.2",
            "tatara-operator": "1.35.1",
            "project-tatara": "1.35.1",
            "project-infrastructure": "1.35.1",
            "project-mtg": "1.35.1",
            **(releases or {}),
        },
        "wrapper_image": wrapper
        or {
            "project-tatara": "v1.2.6",
            "project-infrastructure": "v1.2.6",
            "project-mtg": "v1.2.6",
        },
        "skills_ref": skills
        or {
            "project-tatara": "v1.5.2",
            "project-infrastructure": "v1.5.2",
            "project-mtg": "v1.5.2",
        },
        "operator_image_tag": operator_tag,
    }


def test_uniform_fleet_is_clean():
    assert guard.check(facts()) == []


# --- the regression this guard exists for (#290) ---


def test_catches_the_live_mtg_wrapper_stranding():
    # The exact state of main before this fix: seven wrapper bumps reached
    # tatara + infrastructure, none reached mtg.
    problems = guard.check(
        facts(
            wrapper={
                "project-tatara": "v1.2.6",
                "project-infrastructure": "v1.2.6",
                "project-mtg": "v1.2.0",
            }
        )
    )
    assert len(problems) == 1
    assert (
        "project-mtg is stranded at v1.2.0 while the fleet is at v1.2.6" in problems[0]
    )
    assert "tatara-claude-code-wrapper .github/workflows/release.yml" in problems[0]


def test_catches_the_chart_pin_stranding():
    # The other half of #290: project-mtg absent from the operator's chart pins.
    problems = guard.check(facts(releases={"project-mtg": "1.20.0"}))
    assert len(problems) == 1
    assert (
        "project-mtg is stranded at 1.20.0 while the fleet is at 1.35.1" in problems[0]
    )
    assert "tatara-operator .github/workflows/release.yml" in problems[0]


def test_catches_skills_ref_divergence():
    problems = guard.check(
        facts(skills={**facts()["skills_ref"], "project-mtg": "v1.0.0"})
    )
    assert len(problems) == 1
    assert "agent skillsRef pin" in problems[0]


# --- project-set symmetry (a newly enrolled project is covered automatically) ---


def test_values_project_without_a_release():
    problems = guard.check(
        facts(
            wrapper={**facts()["wrapper_image"], "project-newthing": "v1.2.6"},
            skills={**facts()["skills_ref"], "project-newthing": "v1.5.2"},
        )
    )
    assert any("declares no `- name: project-newthing` release" in p for p in problems)


def test_release_without_values():
    problems = guard.check(facts(releases={"project-orphan": "1.35.1"}))
    assert any("values/project-orphan/common.yaml is missing" in p for p in problems)


# --- the operator writes chart version and image tag from one tag ---


def test_operator_image_tag_must_match_its_chart_pin():
    problems = guard.check(facts(operator_tag="v1.34.0"))
    assert len(problems) == 1
    assert "pins image tag v1.34.0" in problems[0]
    assert "chart at 1.35.1" in problems[0]


def test_operator_chart_drift_from_projects_is_caught():
    problems = guard.check(
        facts(releases={"tatara-operator": "1.34.0"}, operator_tag="v1.34.0")
    )
    assert any("chart pin is not uniform" in p for p in problems)


def test_tatara_chat_version_is_ignored():
    # tatara-chat rides its own chart and is uninstalled; it must not trip the guard.
    assert guard.check(facts(releases={"tatara-chat": "9.9.9"})) == []


def test_even_split_names_no_baseline():
    # A 1-1 split has no majority, so the report must not accuse either side of
    # being the stranded one; it still fails, which is the point.
    two = facts(
        wrapper={"project-tatara": "v1.2.6", "project-infrastructure": "v1.2.0"},
        skills={"project-tatara": "v1.5.2", "project-infrastructure": "v1.5.2"},
    )
    del two["releases"]["project-mtg"]
    problems = guard.check(two)
    assert len(problems) == 1
    assert "agent image pin is not uniform" in problems[0]
    assert "stranded" not in problems[0]


# --- parsing ---


def test_parse_releases_reads_name_and_first_version():
    text = (
        "- name: tatara-chat\n"
        "  chart: oci://harbor/charts/tatara-chat\n"
        "  version: 0.1.2\n"
        "\n"
        "# a comment between releases\n"
        "- name: project-mtg\n"
        "  chart: oci://harbor/charts/tatara-project\n"
        "  namespace: tatara\n"
        "  version: 1.35.1\n"
        "  needs:\n"
        "  - tatara/tatara-operator\n"
    )
    assert guard.parse_releases(text) == {
        "tatara-chat": "0.1.2",
        "project-mtg": "1.35.1",
    }


def test_extract_one_rejects_a_missing_pin():
    with pytest.raises(ValueError):
        guard.extract_one(guard.SKILLS_REF_RE, "nothing here\n", "f", "skillsRef")


def test_extract_one_rejects_a_duplicated_pin():
    with pytest.raises(ValueError):
        guard.extract_one(
            guard.SKILLS_REF_RE, "  skillsRef: v1\n  skillsRef: v2\n", "f", "skillsRef"
        )


# --- against the real repo, so the guard cannot rot away from the pin sites ---


def test_real_repo_pins_parse_and_pass():
    collected = guard.collect(REPO_ROOT)
    assert set(collected["wrapper_image"]) == {
        "project-tatara",
        "project-infrastructure",
        "project-mtg",
    }
    assert guard.check(collected) == []
