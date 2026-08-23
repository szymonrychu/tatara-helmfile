"""Round-trip every CD pin that targets THIS repo, against the REAL file.

WHY THIS EXISTS. A pin is written by a PRODUCER repo's release workflow, which
hands a `pins:` array to `.github/actions/cd-release` (mode=bump). The producer
cannot see the file it rewrites and this repo cannot see the producer's array,
so the regex on one side and the file on the other drift independently. When
they drift, `apply-pins.py` hard-errors - and it does so MID-CASCADE, after
`mode: tag` has already pushed the tag (action.yml:198), leaving a published
release with no deploy pin behind it.

This moves that failure from "red release in a producer repo, after the tag" to
"red lint on the PR that reformatted the pin site". It is the same contract
`check_pin_coverage.py` has carried for `skillsRef` since #397, widened from one
pin to all thirteen that write into this repo.

THE TABLE IS LOCAL AND AUTHORITATIVE-BY-CONVENTION. It is NOT fetched from the
producers: cloning six repos to read their arrays would buy six network
dependencies and a fail-open path, and a guard that can silently stop guarding
is worse than no guard. The cost is that the table MUST be updated by hand when
a producer adds, removes or reshapes a pin. The patterns below are byte-
identical (modulo the JSON escaping of the workflow file) with the producers'
arrays.

NOT COVERED, AND WHY. Seventeen CD pins exist. The four missing here all target
a file in szymonrychu/tatara-claude-code-wrapper, which is neither this repo nor
the repo that writes them:

    producer             parent_repo                   file
    tatara-agent-skills  tatara-claude-code-wrapper    Dockerfile
    tatara-cli           tatara-claude-code-wrapper    Dockerfile
    tatara-cli           tatara-claude-code-wrapper    Makefile
    tatara-cli           tatara-claude-code-wrapper    .github/ci/build.sh

That is the least guarded surface of the three, not the most: two producers
write four pin sites into one repo, two of them into the same Dockerfile, and
nothing anywhere round-trips them. `ARG TATARA_SKILLS_REF` is redeclared at
Dockerfile:127 for a later build stage with no `=`, which is the only reason
`^ARG TATARA_SKILLS_REF=.*$` is count==1 rather than count==2. The wrapper is
where an equivalent table belongs; it cannot live here, because the guard has
to red on the PR that edits the file. Their patterns are exercised
synthetically in .github/actions/cd-release/test_apply_pins.py.
"""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]

_g_spec = importlib.util.spec_from_file_location(
    "check_pin_coverage", HERE / "check_pin_coverage.py"
)
guard = importlib.util.module_from_spec(_g_spec)
_g_spec.loader.exec_module(guard)

_ap_spec = importlib.util.spec_from_file_location(
    "apply_pins", REPO_ROOT / ".github" / "actions" / "cd-release" / "apply-pins.py"
)
apply_pins_mod = importlib.util.module_from_spec(_ap_spec)
_ap_spec.loader.exec_module(apply_pins_mod)
apply_pin = apply_pins_mod.apply_pin

# A version no real pin can already be sitting on, so "exactly one line
# changed" is a real assertion rather than an accident of the current fleet.
NEW_VALUE = "v9.9.9"
BARE = "9.9.9"

SKILLS_PIN_PATTERN = r"^(\s*skillsRef: ).*$"
SKILLS_PIN_VALUE_TEMPLATE = r"\1{{version}}"

WRAPPER_PIN_PATTERN = (
    r"^(\s*image: )harbor\.szymonrichert\.pl/containers/"
    r"tatara-claude-code-wrapper:.*$"
)
WRAPPER_PIN_VALUE_TEMPLATE = (
    r"\1harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:{{version}}"
)

MEMORY_PIN_PATTERN = r'^memoryImage: ".*"$'
MEMORY_PIN_VALUE_TEMPLATE = (
    'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:{{version}}"'
)

INGESTER_PIN_PATTERN = r'^ingesterImage: ".*"$'
INGESTER_PIN_VALUE_TEMPLATE = (
    'ingesterImage: "harbor.szymonrichert.pl/containers/'
    'tatara-memory-repo-ingester:{{version}}"'
)

OPERATOR_TAG_PIN_PATTERN = r'^(\s*tag: )".*"$'
OPERATOR_TAG_PIN_VALUE_TEMPLATE = r'\1"{{image_version}}"'

# The operator's array carries four literal copies of this, differing only in
# the release name. `(?:.*\n)*?` is lazy on purpose: it stops at the FIRST
# `version:` after the named release. Note what that does NOT give you - it is
# not bounded by the release BLOCK, so if the named release ever loses its own
# `version:` line the pattern walks into the next release and rewrites that
# one, still count==1. test_chart_pin_touches_only_its_own_release is the
# assertion that catches it.
CHART_PIN_VALUE_TEMPLATE = r"\1{{chart_version_bare}}"


def _chart_pin_pattern(release):
    return r"(- name: " + release + r"\n(?:.*\n)*?\s*version: )\S+"


def _skills_ref(text):
    return guard.extract_one(guard.SKILLS_REF_RE, text, "rewritten", "skillsRef")


def _wrapper_tag(text):
    return guard.extract_one(guard.WRAPPER_IMAGE_RE, text, "rewritten", "wrapper image")


def _operator_tag(text):
    return guard.extract_one(guard.OPERATOR_TAG_RE, text, "rewritten", "image tag")


def _chart_reader(release):
    return lambda text: guard.parse_releases(text)[release]


PROJECTS = ("project-tatara", "project-infrastructure", "project-mtg")

# Each row is one pin.
#
#   line     the EXACT substring the rewritten line must carry. Spelled out per
#            row rather than reusing NEW_VALUE, because "9.9.9" is a substring
#            of "v9.9.9": a chart pin that regressed from
#            {{chart_version_bare}} to {{version}} would pass a bare
#            containment check.
#   reader   the CONSUMER half of the contract - the regex THIS repo reads the
#            pin back with. Where one exists, the rewritten value must survive
#            it, which is what catches a value_template that still matches once
#            but drops a quote or trails a comment. The two
#            values/tatara-operator/default.yaml pins have no consumer-side
#            regex (they are not per-project uniformity pins), so they assert
#            shape and idempotence only.
CD_PINS = [
    *[
        {
            "label": "skillsRef",
            "producer": "tatara-agent-skills",
            "file": f"values/{p}/common.yaml",
            "pattern": SKILLS_PIN_PATTERN,
            "value_template": SKILLS_PIN_VALUE_TEMPLATE,
            "line": f"skillsRef: {NEW_VALUE}",
            "reader": _skills_ref,
            "read_back": NEW_VALUE,
        }
        for p in PROJECTS
    ],
    *[
        {
            "label": "wrapper image",
            "producer": "tatara-claude-code-wrapper",
            "file": f"values/{p}/common.yaml",
            "pattern": WRAPPER_PIN_PATTERN,
            "value_template": WRAPPER_PIN_VALUE_TEMPLATE,
            "line": f"tatara-claude-code-wrapper:{NEW_VALUE}",
            "reader": _wrapper_tag,
            "read_back": NEW_VALUE,
        }
        for p in PROJECTS
    ],
    {
        "label": "memoryImage",
        "producer": "tatara-memory",
        "file": "values/tatara-operator/default.yaml",
        "pattern": MEMORY_PIN_PATTERN,
        "value_template": MEMORY_PIN_VALUE_TEMPLATE,
        "line": f'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:{NEW_VALUE}"',
        "reader": None,
        "read_back": None,
    },
    {
        "label": "ingesterImage",
        "producer": "tatara-memory-repo-ingester",
        "file": "values/tatara-operator/default.yaml",
        "pattern": INGESTER_PIN_PATTERN,
        "value_template": INGESTER_PIN_VALUE_TEMPLATE,
        "line": f'ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:{NEW_VALUE}"',
        "reader": None,
        "read_back": None,
    },
    *[
        {
            "label": f"{release} chart",
            "producer": "tatara-operator",
            "file": "helmfile.yaml.gotmpl",
            "pattern": _chart_pin_pattern(release),
            "value_template": CHART_PIN_VALUE_TEMPLATE,
            "line": f"version: {BARE}",
            "reader": _chart_reader(release),
            "read_back": BARE,
            "release": release,
        }
        for release in ("tatara-operator", *PROJECTS)
    ],
    {
        "label": "operator image tag",
        "producer": "tatara-operator",
        "file": "values/tatara-operator/common.yaml",
        "pattern": OPERATOR_TAG_PIN_PATTERN,
        "value_template": OPERATOR_TAG_PIN_VALUE_TEMPLATE,
        "line": f'tag: "{NEW_VALUE}"',
        "reader": _operator_tag,
        "read_back": NEW_VALUE,
    },
]

CHART_PINS = [pin for pin in CD_PINS if "release" in pin]

PARAMS = pytest.mark.parametrize(
    "pin", CD_PINS, ids=[f"{p['producer']}:{p['file']}:{p['label']}" for p in CD_PINS]
)
CHART_PARAMS = pytest.mark.parametrize(
    "pin", CHART_PINS, ids=[p["label"] for p in CHART_PINS]
)


def _real(path):
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _rewrite(pin):
    return apply_pin(
        _real(pin["file"]),
        pin["pattern"],
        pin["value_template"],
        NEW_VALUE,
        path=pin["file"],
    )


def test_the_table_covers_thirteen_pins():
    """A row silently dropped from the table is a pin silently unguarded."""
    assert len(CD_PINS) == 13


def test_the_table_covers_every_enrolled_project():
    """A newly enrolled project must red HERE, not in a producer's release run.

    check_pin_coverage.py catches a project missing from a producer's array by
    uniformity; this catches it missing from the table that is supposed to be
    checking the producers.
    """
    on_disk = {p.parent.name for p in (REPO_ROOT / "values").glob("project-*/common.yaml")}
    assert on_disk == set(PROJECTS)


@PARAMS
def test_pin_matches_its_real_file_exactly_once(pin):
    # apply_pin defaults to expect=1 and raises on anything else, so no
    # separate count assertion is needed: this call IS the assertion.
    _rewrite(pin)


@PARAMS
def test_pin_rewrite_preserves_shape(pin):
    before = _real(pin["file"]).split("\n")
    after = _rewrite(pin).split("\n")

    assert len(before) == len(after)
    for old, new in zip(before, after):
        assert old[: len(old) - len(old.lstrip())] == new[: len(new) - len(new.lstrip())]

    differing = [(old, new) for old, new in zip(before, after) if old != new]
    assert len(differing) == 1
    assert pin["line"] in differing[0][1]


@PARAMS
def test_pin_rewrite_is_idempotent(pin):
    # The second call goes through the same exactly-once check, so a template
    # that damages the line it writes (dropping a quote under
    # `^(\s*tag: )".*"$`, say) fails here as count==0 rather than shipping.
    once = _rewrite(pin)
    assert apply_pin(once, pin["pattern"], pin["value_template"], NEW_VALUE,
                     path=pin["file"]) == once


@PARAMS
def test_rewritten_value_is_readable_by_the_consumer_regex(pin):
    if pin["reader"] is None:
        pytest.skip(f"{pin['label']} has no consumer-side regex in check_pin_coverage.py")
    assert pin["reader"](_rewrite(pin)) == pin["read_back"]


def _release_block(lines, release):
    """[start, end) line indices of `- name: <release>` up to the next release."""
    start = lines.index(f"- name: {release}")
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("- name: "):
            return start, i
    return start, len(lines)


@CHART_PARAMS
def test_chart_pin_touches_only_its_own_release(pin):
    """The chart pattern is not bounded by the release BLOCK.

    `(?:.*\\n)*?` stops at the first `version:` AFTER the named release, not at
    the end of its block. So a release that loses its own `version:` line has
    its pin quietly rewrite the NEXT release's - one match, one changed line,
    indentation intact, idempotent. Every other assertion in this file is green
    on that tree.

    guard.parse_releases cannot be the witness: it is built from the same lazy
    construct on purpose (so the guard reads pins the way CD writes them) and
    inherits the same blind spot - finditer's match for the broken release
    swallows the next `- name:` line, and the neighbour simply disappears from
    the map rather than showing a changed version. Line position is the only
    thing that actually knows where the block ends.
    """
    lines = _real(pin["file"]).split("\n")
    after = _rewrite(pin).split("\n")
    changed = [i for i, (old, new) in enumerate(zip(lines, after)) if old != new]
    start, end = _release_block(lines, pin["release"])
    assert len(changed) == 1
    assert start < changed[0] < end, (
        f"{pin['release']}'s chart pin rewrote line {changed[0] + 1}, which is "
        f"outside its block (lines {start + 1}-{end}): {after[changed[0]]!r}"
    )


def test_skills_pin_rewrite_preserves_indentation():
    """Synthetic counterpart to the real-file rows: `\\1` must carry the indent."""
    text = "project:\n  spec:\n    agent:\n      skillsRef: v2.4.0\n"
    once = apply_pin(text, SKILLS_PIN_PATTERN, SKILLS_PIN_VALUE_TEMPLATE, "v2.5.0")
    assert once == "project:\n  spec:\n    agent:\n      skillsRef: v2.5.0\n"


def test_a_second_pin_site_in_a_values_file_is_a_hard_error():
    """The arity half of the contract, on the shape a real file would take.

    A project values file that grew a second `skillsRef` (a per-kind agent
    override, say) must fail the producer's bump rather than have both keys
    rewritten from one release. The producer's route back to green is
    `"expect": 2` in its array, not deleting the check.
    """
    text = "agents:\n  default:\n    skillsRef: v2.4.0\n  research:\n    skillsRef: v2.4.0\n"
    with pytest.raises(ValueError) as excinfo:
        apply_pin(text, SKILLS_PIN_PATTERN, SKILLS_PIN_VALUE_TEMPLATE, "v2.5.0",
                  path="values/project-tatara/common.yaml")
    assert "matched 2 times" in str(excinfo.value)
    assert apply_pin(text, SKILLS_PIN_PATTERN, SKILLS_PIN_VALUE_TEMPLATE, "v2.5.0", expect=2) == (
        "agents:\n  default:\n    skillsRef: v2.5.0\n  research:\n    skillsRef: v2.5.0\n"
    )
