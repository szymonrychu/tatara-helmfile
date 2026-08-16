import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]
SCRIPT = HERE / "check_pin_coverage.py"
_spec = importlib.util.spec_from_file_location("check_pin_coverage", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

APPLY_PINS = REPO_ROOT / ".github" / "actions" / "cd-release" / "apply-pins.py"
_ap_spec = importlib.util.spec_from_file_location("apply_pins", APPLY_PINS)
apply_pins_mod = importlib.util.module_from_spec(_ap_spec)
_ap_spec.loader.exec_module(apply_pins_mod)


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


# --- floating / absent agent pins (a skills or wrapper merge must not ship
# --- live to a Project just because its pin was never set) ---


def test_agent_pins_must_be_concrete(tmp_path):
    """A floating skillsRef or wrapper tag ships every upstream merge live."""
    values = tmp_path / "values" / "project-x"
    values.mkdir(parents=True)
    (values / "common.yaml").write_text(
        "project:\n"
        "  spec:\n"
        "    agent:\n"
        "      image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:latest\n"
        "      skillsRef: main\n"
    )
    problems = guard.check_agent_pins(tmp_path)
    assert len(problems) == 2
    assert any("skillsRef" in p for p in problems)
    assert any("wrapper image tag" in p for p in problems)


def test_agent_pins_accept_a_semver_tag(tmp_path):
    values = tmp_path / "values" / "project-x"
    values.mkdir(parents=True)
    (values / "common.yaml").write_text(
        "project:\n"
        "  spec:\n"
        "    agent:\n"
        "      image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:v1.3.5\n"
        "      skillsRef: v1.8.0\n"
    )
    assert guard.check_agent_pins(tmp_path) == []


def test_agent_pins_reject_an_absent_skillsref(tmp_path):
    """Absent is worse than floating: pod.go defaults TATARA_SKILLS_REF to 'main'."""
    values = tmp_path / "values" / "project-x"
    values.mkdir(parents=True)
    (values / "common.yaml").write_text(
        "project:\n"
        "  spec:\n"
        "    agent:\n"
        "      image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:v1.3.5\n"
    )
    problems = guard.check_agent_pins(tmp_path)
    assert len(problems) == 1
    assert "skillsRef is absent" in problems[0]


def test_agent_pins_reject_an_absent_wrapper_image(tmp_path):
    values = tmp_path / "values" / "project-x"
    values.mkdir(parents=True)
    (values / "common.yaml").write_text(
        "project:\n  spec:\n    agent:\n      skillsRef: v1.8.0\n"
    )
    problems = guard.check_agent_pins(tmp_path)
    assert len(problems) == 1
    assert "wrapper image tag is absent" in problems[0]


def test_agent_pins_no_project_files_is_clean(tmp_path):
    (tmp_path / "values").mkdir()
    assert guard.check_agent_pins(tmp_path) == []


# --- against the real repo, so the guard cannot rot away from the pin sites ---


def test_real_repo_pins_parse_and_pass():
    collected = guard.collect(REPO_ROOT)
    assert set(collected["wrapper_image"]) == {
        "project-tatara",
        "project-infrastructure",
        "project-mtg",
    }
    assert guard.check(collected) == []


def test_real_repo_agent_pins_are_all_concrete():
    assert guard.check_agent_pins(REPO_ROOT) == []


# --- the cross-repo skillsRef pin contract (#397) ---
#
# The producer half of these two constants lives in
# tatara-agent-skills/.github/workflows/release.yml. Nothing in that repo can
# see SKILLS_REF_RE, and nothing here can see the pins array, so the contract
# between them is enforced by exercising both halves against the real files.


def test_skills_pin_pattern_rewrites_every_real_values_file():
    """The producer's pin pattern must match each real pin site exactly once.

    apply-pins.py treats a pattern matching nothing as a hard error, so a
    reformat of a skillsRef line that slipped past review would fail the whole
    deploy train in tatara-agent-skills' release run rather than here.
    """
    for project in ("project-tatara", "project-infrastructure", "project-mtg"):
        text = (REPO_ROOT / "values" / project / "common.yaml").read_text(
            encoding="utf-8"
        )
        rewritten = apply_pins_mod.apply_pin(
            text, guard.SKILLS_PIN_PATTERN, guard.SKILLS_PIN_VALUE_TEMPLATE, "v9.9.9"
        )
        assert (
            guard.extract_one(
                guard.SKILLS_REF_RE, rewritten, project, "skillsRef"
            )
            == "v9.9.9"
        )


def test_skills_pin_rewrite_preserves_indentation_and_is_idempotent():
    text = "project:\n  spec:\n    agent:\n      skillsRef: v2.4.0\n"
    once = apply_pins_mod.apply_pin(
        text, guard.SKILLS_PIN_PATTERN, guard.SKILLS_PIN_VALUE_TEMPLATE, "v2.5.0"
    )
    assert once == "project:\n  spec:\n    agent:\n      skillsRef: v2.5.0\n"
    twice = apply_pins_mod.apply_pin(
        once, guard.SKILLS_PIN_PATTERN, guard.SKILLS_PIN_VALUE_TEMPLATE, "v2.5.0"
    )
    assert twice == once


def test_skills_ref_re_rejects_a_trailing_comment_on_the_pin_line():
    """`(\\S+)$` is load-bearing: it refuses what `(.*)$` would swallow.

    A trailing comment would be captured whole by a laxer regex and shipped as
    the pin value; here it is a malformed pin site and extract_one raises.
    """
    with pytest.raises(ValueError):
        guard.extract_one(
            guard.SKILLS_REF_RE,
            "      skillsRef: v2.4.0  # bumped by hand\n",
            "f",
            "skillsRef",
        )
