#!/usr/bin/env python3
"""Completeness guard for the tatara CD pin fan-out.

Every per-project pin in this repo is written by a PRODUCER repo's release
workflow, which hands a `pins:` array to
`.github/actions/cd-release` (mode=bump). `apply-pins.py` makes a pin whose
pattern matches nothing a hard error - but a file that is simply ABSENT from
the array is invisible: it is never rewritten, never diffed, never reported.
That project then freezes at whatever version it was last hand-set to, and
nothing anywhere goes red.

That is exactly how `project-mtg` sat on tatara-claude-code-wrapper v1.2.0 for
seven consecutive releases while the rest of the fleet ran v1.2.6 - every
memory/code-graph MCP call from an mtg agent 404ed for five days with a green
pipeline and a green MemoryReady (#290; mechanism described in #279).

THE INVARIANT: a producer writes ONE value into ALL of its listed pins in ONE
commit. So every CD-managed per-project pin MUST be identical across projects.
Divergence has exactly one cause - a project missing from a pin list - which
turns "silently absent" into a red check. The project SET is also checked on
both sides of the repo (values tree vs helmfile releases), so a newly enrolled
project is covered the moment its values directory lands.

WHAT THIS PROVES, AND WHAT IT DOES NOT: it proves the fan-out is COMPLETE and
UNIFORM, never that it is CURRENT. A pin lagging the newest release is normal
between deploy trains, so a currency assertion here would be red during normal
operation - and it would need a network fetch, which would cost this guard its
offline/stdlib contract and its pre-commit path. Currency is a separate,
non-blocking, scheduled check: .github/scripts/check_skills_currency.py.

That split has one blind spot worth naming, because it is the whole of #397:
three projects agreeing on a stale value is green here forever. Uniformity is
only evidence of a working fan-out while a producer actually writes the pin.
Until #397 nothing wrote `skillsRef`, so this check passed for eight days over
a fleet three skills releases behind.

Read-only, stdlib-only, no cluster access. Exit 0 clean, 1 with a report.
"""

import re
import sys
from collections import Counter
from pathlib import Path

HELMFILE = "helmfile.yaml.gotmpl"

# Release name -> its chart `version:`. Same lazy construct the operator's own
# pin patterns use, so the guard reads the pins the way CD writes them.
RELEASE_VERSION_RE = re.compile(
    r"^- name: (\S+)\n(?:.*\n)*?\s*version: (\S+)$", re.MULTILINE
)

WRAPPER_IMAGE_RE = re.compile(
    r"^\s*image: harbor\.szymonrichert\.pl/containers/tatara-claude-code-wrapper:(\S+)$",
    re.MULTILINE,
)
# CROSS-REPO CONTRACT. The producer that rewrites this pin lives in another
# repo (tatara-agent-skills .github/workflows/release.yml) and cannot see this
# regex; this regex cannot see its pins array. `(\S+)$` is the load-bearing
# part: it accepts exactly the shape SKILLS_PIN_VALUE_TEMPLATE below writes -
# one line, one non-whitespace token, nothing trailing. Relaxing it to `(.*)$`
# would silently swallow a trailing comment and ship it as the pin value.
# test_check_pin_coverage.py exercises both halves against the real values
# files, so a reformat of a pin site reds THIS repo's lint instead of
# hard-erroring apply-pins.py with count==0 mid-release in tatara-agent-skills.
SKILLS_REF_RE = re.compile(r"^\s*skillsRef: (\S+)$", re.MULTILINE)

# The producer's half of that contract, kept here so the test above can run it.
# Must stay byte-identical (modulo the JSON/YAML escaping of the workflow file)
# with the `pins:` array of the `bump tatara-helmfile skillsRef` step in
# tatara-agent-skills .github/workflows/release.yml.
SKILLS_PIN_PATTERN = r"^(\s*skillsRef: ).*$"
SKILLS_PIN_VALUE_TEMPLATE = r"\1{{version}}"
OPERATOR_TAG_RE = re.compile(r'^\s*tag: "(\S+)"$', re.MULTILINE)

# A concrete pin is a vX.Y.Z tag. "main", "latest", a branch name or an
# absent value all mean "whatever upstream merged last", which is how a
# skills or wrapper merge ships live to a running fleet the moment it lands
# (tatara-operator internal/agent/pod.go:733-742 defaults TATARA_SKILLS_REF
# to "main" when spec.agent.skillsRef is empty). Uniformity across projects
# (checked below) says nothing about this: three projects agreeing on
# `skillsRef: main` would pass check_uniform and still ship live.
SEMVER_TAG = re.compile(r"^v\d+\.\d+\.\d+$")

WRAPPER_PRODUCER = (
    "szymonrychu/tatara-claude-code-wrapper .github/workflows/release.yml, "
    "the `pins:` array of the `bump tatara-helmfile` step"
)
OPERATOR_PRODUCER = (
    "szymonrychu/tatara-operator .github/workflows/release.yml, "
    "the `pins:` array of the `bump` job"
)
SKILLS_PRODUCER = (
    "szymonrychu/tatara-agent-skills .github/workflows/release.yml, "
    "the `pins:` array of the `bump tatara-helmfile skillsRef` step"
)


def parse_releases(text):
    """helmfile.yaml.gotmpl text -> {release name: chart version}."""
    return {m.group(1): m.group(2) for m in RELEASE_VERSION_RE.finditer(text)}


def extract_one(regex, text, path, field):
    """Exactly-one-match extraction; anything else is a malformed pin site."""
    found = regex.findall(text)
    if len(found) != 1:
        raise ValueError(
            f"{path}: expected exactly one {field} pin, found {len(found)}"
        )
    return found[0]


def strip_v(value):
    return value[1:] if value.startswith("v") else value


def _majority(values):
    """The value held by a strict majority, else None (a 1-1 split names no baseline)."""
    top, count = Counter(values).most_common(1)[0]
    return top if count * 2 > len(values) else None


def check_uniform(label, by_project, producer):
    """Every project must carry the same value for a CD-managed pin."""
    if len(set(by_project.values())) <= 1:
        return []
    shown = ", ".join(f"{p}={v}" for p, v in sorted(by_project.items()))
    lines = [f"{label} is not uniform across projects: {shown}"]
    baseline = _majority(list(by_project.values()))
    if baseline is not None:
        for project, value in sorted(by_project.items()):
            if value != baseline:
                lines.append(
                    f"    {project} is stranded at {value} while the fleet is at {baseline}"
                )
    lines.append(
        "    A CD bump writes one value into every listed pin in a single "
        "commit, so these can only diverge when a project is MISSING from the "
        f"producer's pin list. Add it to: {producer}"
    )
    return ["\n".join(lines)]


def check(facts):
    """facts -> list of human-readable problems (empty means the fan-out is complete)."""
    problems = []

    values_projects = set(facts["wrapper_image"])
    release_projects = {n for n in facts["releases"] if n.startswith("project-")}

    for project in sorted(values_projects - release_projects):
        problems.append(
            f"values/{project}/common.yaml exists but {HELMFILE} declares no "
            f"`- name: {project}` release, so the project is never deployed and no "
            "chart pin can reach it."
        )
    for project in sorted(release_projects - values_projects):
        problems.append(
            f"{HELMFILE} declares release {project} but values/{project}/common.yaml "
            "is missing, so it carries no agent image pin."
        )

    # The operator release writes all four chart pins plus its own image tag
    # from one tag, atomically. They must agree or a project was skipped.
    chart_pins = {p: facts["releases"][p] for p in sorted(release_projects)}
    chart_pins["tatara-operator"] = facts["releases"]["tatara-operator"]
    problems += check_uniform(
        "tatara-project/operator chart pin", chart_pins, OPERATOR_PRODUCER
    )

    operator_tag = strip_v(facts["operator_image_tag"])
    operator_chart = facts["releases"]["tatara-operator"]
    if operator_tag != operator_chart:
        problems.append(
            f"values/tatara-operator/common.yaml pins image tag "
            f"{facts['operator_image_tag']} but {HELMFILE} pins the tatara-operator "
            f"chart at {operator_chart}; the operator release writes both from one "
            f"tag, so they cannot legitimately differ. Producer: {OPERATOR_PRODUCER}"
        )

    problems += check_uniform(
        "tatara-claude-code-wrapper agent image pin",
        facts["wrapper_image"],
        WRAPPER_PRODUCER,
    )
    problems += check_uniform(
        "agent skillsRef pin", facts["skills_ref"], SKILLS_PRODUCER
    )

    return problems


def check_agent_pins(root):
    """Return a list of human-readable problems, one per unpinned agent input.

    Independent of collect()/check(): those require exactly one match per
    file and raise ValueError on anything else, which is right for the
    uniformity check but would abort before this one ever ran. This walks
    the same values/project-*/common.yaml files with the same regexes but
    never raises, so an absent or floating pin is reported, not a crash.
    """
    problems = []
    for path in sorted(Path(root).glob("values/project-*/common.yaml")):
        rel = f"values/{path.parent.name}/common.yaml"
        text = path.read_text(encoding="utf-8")

        refs = SKILLS_REF_RE.findall(text)
        if not refs:
            problems.append(
                f"{rel}: skillsRef is absent; the operator defaults it to 'main'"
            )
        else:
            for ref in refs:
                if not SEMVER_TAG.match(ref):
                    problems.append(f"{rel}: skillsRef={ref!r} is not a vX.Y.Z tag")

        tags = WRAPPER_IMAGE_RE.findall(text)
        if not tags:
            problems.append(
                f"{rel}: wrapper image tag is absent (no tatara-claude-code-wrapper image pin found)"
            )
        else:
            for tag in tags:
                if not SEMVER_TAG.match(tag):
                    problems.append(
                        f"{rel}: wrapper image tag {tag!r} is not a vX.Y.Z tag"
                    )
    return problems


def collect(root):
    """Read the repo's pin sites into a plain facts dict."""
    root = Path(root)
    helmfile_text = (root / HELMFILE).read_text(encoding="utf-8")
    releases = parse_releases(helmfile_text)
    if "tatara-operator" not in releases:
        raise ValueError(f"{HELMFILE}: no `- name: tatara-operator` release found")

    wrapper_image, skills_ref = {}, {}
    for values_file in sorted((root / "values").glob("project-*/common.yaml")):
        project = values_file.parent.name
        text = values_file.read_text(encoding="utf-8")
        rel = f"values/{project}/common.yaml"
        wrapper_image[project] = extract_one(
            WRAPPER_IMAGE_RE, text, rel, "wrapper image"
        )
        skills_ref[project] = extract_one(SKILLS_REF_RE, text, rel, "skillsRef")
    if not wrapper_image:
        raise ValueError("no values/project-*/common.yaml files found")

    operator_values = root / "values" / "tatara-operator" / "common.yaml"
    operator_image_tag = extract_one(
        OPERATOR_TAG_RE,
        operator_values.read_text(encoding="utf-8"),
        "values/tatara-operator/common.yaml",
        "image tag",
    )

    return {
        "releases": releases,
        "wrapper_image": wrapper_image,
        "skills_ref": skills_ref,
        "operator_image_tag": operator_image_tag,
    }


def main(argv):
    root = argv[0] if argv else str(Path(__file__).resolve().parents[2])
    # check_agent_pins() never raises, so it always contributes its findings,
    # even when collect() below aborts on a file too malformed to parse.
    problems = check_agent_pins(root)
    try:
        problems += check(collect(root))
    except ValueError as e:
        problems.append(f"pin-coverage guard could not read the pins: {e}")
    if problems:
        sys.stderr.write(
            "::error::CD pin fan-out is INCOMPLETE - a project is drifting.\n"
        )
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
        return 1
    print("CD pin fan-out complete: every project carries identical CD-managed pins.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
