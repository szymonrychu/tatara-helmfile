#!/usr/bin/env python3
"""Currency check for `agent.skillsRef` against tatara-agent-skills' tags.

check_pin_coverage.py proves the skills fan-out is COMPLETE and UNIFORM. It
cannot prove it is CURRENT: three projects agreeing on a stale value are green
there forever. That is exactly how #397 happened - nothing wrote the pin for
eight days and every project agreed on how far behind it was.

This closes the one failure mode uniformity structurally cannot see: the
producer hop deleted, renamed, or never wired at all. It reads the published
tag list of the producing repo and reds when a project's pin is 2 or more
published tags behind the newest.

WHY 2 AND NOT 1: one tag behind is a deploy train in flight. The skills release
cuts its tag, then opens the bump PR against cd/deploy-train, which merges on a
green helmfile diff - so between those two events the fleet is legitimately one
release behind. Redding on 1 would mean this check is red during normal
operation, which is how a check gets weakened to a warning and then deleted.

WHY THIS IS NOT A MERGE GATE. It fetches, so it cannot live in
check_pin_coverage.py without costing that guard its offline/stdlib contract
and its pre-commit path. It is wired to a schedule, not to pull_request:
staleness is a fleet condition, never a property of the PR in front of you.

FAIL-CLOSED. A fetch that fails, a remote with no semver tags, a values tree
with no projects, or a pin naming no published tag are all reported. A currency
check that silently stops checking is worse than no currency check.

Exit 0 clean, 1 with a report.
"""

import re
import subprocess
import sys
from pathlib import Path

SKILLS_REMOTE = "https://github.com/szymonrychu/tatara-agent-skills.git"

# Lag, in published tags, that this check tolerates. See "WHY 2 AND NOT 1".
MAX_LAG = 1

SKILLS_REF_RE = re.compile(r"^\s*skillsRef: (\S+)$", re.MULTILINE)
TAG_RE = re.compile(r"^\S+\srefs/tags/(v\d+\.\d+\.\d+)$", re.MULTILINE)


class FetchError(RuntimeError):
    """The tag list could not be read. Never degraded into an empty list."""


def _git_ls_remote(argv):
    out = subprocess.run(
        argv, capture_output=True, text=True, timeout=60, check=False
    )
    if out.returncode != 0:
        raise FetchError(f"{' '.join(argv)} exited {out.returncode}: {out.stderr.strip()}")
    return out.stdout


def fetch_tags(remote, runner=_git_ls_remote):
    """Raw `git ls-remote --tags` output. Public repo, so no token is needed."""
    try:
        return runner(["git", "ls-remote", "--tags", remote])
    except FetchError:
        raise
    except Exception as e:
        raise FetchError(f"could not read tags from {remote}: {e}") from e


def parse_tags(text):
    """ls-remote output -> semver tags, oldest first.

    Drops `^{}` peeled entries (annotated tags list twice), non-tag refs, and
    anything that is not vX.Y.Z - a release this pin could never legitimately
    hold is not a yardstick for how far behind it is.
    """
    tags = {m.group(1) for m in TAG_RE.finditer(text)}
    return sorted(tags, key=lambda t: tuple(int(p) for p in t[1:].split(".")))


def read_pins(root):
    """{project: skillsRef} for every values/project-*/common.yaml."""
    pins = {}
    for path in sorted(Path(root).glob("values/project-*/common.yaml")):
        found = SKILLS_REF_RE.findall(path.read_text(encoding="utf-8"))
        if len(found) == 1:
            pins[path.parent.name] = found[0]
    return pins


def evaluate(pins_by_project, tags, max_lag=MAX_LAG):
    """pins + published tags -> list of human-readable problems."""
    if not tags:
        return [
            f"{SKILLS_REMOTE} published no semver tags; the currency check has "
            "nothing to compare against, which is a broken check, not a clean fleet."
        ]
    if not pins_by_project:
        return [
            "no values/project-*/common.yaml carried a readable skillsRef; "
            "nothing was checked."
        ]

    newest = tags[-1]
    index = {tag: i for i, tag in enumerate(tags)}
    problems = []
    for project, pin in sorted(pins_by_project.items()):
        if pin not in index:
            problems.append(
                f"values/{project}/common.yaml pins skillsRef={pin} which names no "
                f"published tatara-agent-skills tag. Either the tag was deleted or "
                f"the pin was hand-written; an agent pod cloning it gets nothing."
            )
            continue
        lag = len(tags) - 1 - index[pin]
        if lag > max_lag:
            problems.append(
                f"values/{project}/common.yaml pins skillsRef={pin}, {lag} published "
                f"tags behind {newest}. A lag over {max_lag} is not a deploy train in "
                f"flight: the bump hop in szymonrychu/tatara-agent-skills "
                f".github/workflows/release.yml (the `pins:` array of the `bump "
                f"tatara-helmfile skillsRef` step) is not reaching this repo."
            )
    return problems


def main(argv):
    root = argv[0] if argv else str(Path(__file__).resolve().parents[2])
    try:
        tags = parse_tags(fetch_tags(SKILLS_REMOTE))
    except FetchError as e:
        sys.stderr.write(f"::error::skills currency check could not fetch tags: {e}\n")
        return 1

    problems = evaluate(read_pins(root), tags)
    if problems:
        sys.stderr.write(
            "::error::agent.skillsRef is stale - the skills CD hop is not reaching "
            "this repo.\n"
        )
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
        return 1
    print(f"agent.skillsRef is current with {SKILLS_REMOTE} ({tags[-1]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
