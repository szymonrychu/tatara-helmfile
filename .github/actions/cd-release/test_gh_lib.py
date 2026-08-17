"""gh-lib.sh: the forge helpers must fail LOUD, never quietly produce nothing.

Run 32035597231 attempt 4 (2026-08-17, GitHub partial outage): `gh pr list` 503
was eaten by `|| true`, `gh pr create` 503 next, and its failure did not
propagate - bash unsets errexit inside a command substitution unless
`inherit_errexit` is on, so `open_or_reuse_pr` printed an EMPTY url and returned
0. The pin was already pushed to cd/deploy-train; the job then POSTed
`issues//labels`, 404'd, and died leaving a pushed pin behind no PR and nothing
armed. Nothing could deploy until a human opened the PR.

So the contract these tests pin is narrow and absolute:

  - a forge read that FAILED is never reported as "no PR exists";
  - no helper ever prints an empty/partial url;
  - a failure reaches the CALLING script's exit status, through a command
    substitution;
  - the writer asserts auto-merge is armed by READING IT BACK, because
    `gh pr merge --auto` exiting 0 is not evidence.

`gh` is stubbed on PATH, scripted per subcommand, so the real bash runs.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).parent
LIB = HERE / "gh-lib.sh"
ACTION = HERE / "action.yml"

STUB_GH = """#!/usr/bin/env bash
# Scripted `gh`. Key is "$1" or "$1-$2" when $2 is not a flag: pr-list,
# pr-create, pr-view, pr-merge, label-create, api. Each key reads
# "$GH_STUB_DIR/<key>.plan": one line per invocation, "<exit> [stdout]".
# The last line repeats once the plan is exhausted.
key="$1"
if [ -n "${2-}" ] && [ "${2#-}" = "$2" ]; then key="$1-$2"; fi
printf '%s %s\\n' "$key" "$*" >> "$GH_STUB_DIR/calls.log"
plan="$GH_STUB_DIR/$key.plan"
n_file="$GH_STUB_DIR/$key.n"
n=$(( $(cat "$n_file" 2>/dev/null || echo 0) + 1 ))
printf '%s' "$n" > "$n_file"
[ -f "$plan" ] || exit 0
line="$(sed -n "${n}p" "$plan")"
[ -n "$line" ] || line="$(tail -n 1 "$plan")"
code="${line%% *}"
out="${line#* }"
[ "$out" = "$line" ] && out=""
[ -n "$out" ] && printf '%s\\n' "$out"
exit "$code"
"""

URL = "https://github.com/szymonrychu/tatara-helmfile/pull/423"


class Stub:
    def __init__(self, root):
        self.dir = root
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        gh = self.bin / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)

    def plan(self, key, lines):
        (self.dir / f"{key}.plan").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def calls(self, key=None):
        log = self.dir / "calls.log"
        if not log.exists():
            return []
        got = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln]
        if key is None:
            return got
        return [ln for ln in got if ln.split(" ", 1)[0] == key]

    def run(self, snippet):
        """Run `snippet` in a script shaped exactly like an action.yml step."""
        script = (
            "set -euo pipefail\n"
            "shopt -s inherit_errexit\n"
            f'source "{LIB}"\n' + textwrap.dedent(snippet)
        )
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_STUB_DIR": str(self.dir),
            "GH_RETRY_ATTEMPTS": "3",
            "GH_RETRY_SLEEP": "0",
        }
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
        )


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path)


# --- open_or_reuse_pr -------------------------------------------------------

OPEN = f'open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train "cd: deploy-train" body'


def test_reuses_the_open_pr_without_creating_a_second(stub):
    stub.plan("pr-list", [f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert stub.calls("pr-create") == []


def test_creates_when_the_list_succeeded_and_found_nothing(stub):
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", [f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-create")) == 1


def test_a_failed_list_is_not_reported_as_no_pr_exists(stub):
    """The 2026-08-17 defect: `|| true` turned a 503 into "open a new one"."""
    stub.plan("pr-list", ["1", "1", "1"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode != 0
    assert r.stdout.strip() == ""
    assert stub.calls("pr-create") == [], "must not create a PR it could not rule out"


def test_a_transient_list_failure_is_retried(stub):
    stub.plan("pr-list", ["1", f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-list")) == 2


def test_a_transient_create_failure_is_retried(stub):
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", ["1", f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-create")) == 2


@pytest.mark.parametrize(
    "name,list_plan,create_plan",
    [
        ("list down", ["1", "1", "1"], [f"0 {URL}"]),
        ("create down", ["0"], ["1", "1", "1"]),
        ("create returns nothing", ["0"], ["0"]),
        ("create returns a non-pr url", ["0"], ["0 https://github.com/o/r/issues/9"]),
        ("list returns a non-pr url", ["0 https://example.com/nope"], [f"0 {URL}"]),
    ],
)
def test_never_prints_an_empty_or_bogus_url(stub, name, list_plan, create_plan):
    """Whatever goes wrong, the caller gets a nonzero status and NO url."""
    stub.plan("pr-list", list_plan)
    stub.plan("pr-create", create_plan)
    r = stub.run(f"{OPEN}\n")
    assert r.returncode != 0, name
    assert r.stdout.strip() == "", name


# --- the caller contract ----------------------------------------------------


def test_failure_propagates_through_the_command_substitution(stub):
    """`pr_url="$(open_or_reuse_pr ...)" || exit 1` must stop the CALLING script.

    This is the half `set -e` does not give you: without `inherit_errexit` the
    substitution subshell runs with errexit off, so only an explicit nonzero
    return from the function stops the caller.
    """
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", ["1", "1", "1"])
    r = stub.run(
        """
        pr_url="$(open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train t b)" || exit 1
        echo "REACHED_ARM url=[$pr_url]"
        """
    )
    assert r.returncode != 0
    assert "REACHED_ARM" not in r.stdout, "armed a PR that was never opened"


def test_success_reaches_the_caller_intact(stub):
    stub.plan("pr-list", [f"0 {URL}"])
    r = stub.run(
        """
        pr_url="$(open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train t b)" || exit 1
        echo "REACHED_ARM url=[$pr_url]"
        """
    )
    assert r.returncode == 0
    assert f"REACHED_ARM url=[{URL}]" in r.stdout


# --- pr_number --------------------------------------------------------------


@pytest.mark.parametrize(
    "url,want",
    [
        (URL, "423"),
        ("https://github.com/o/r/pull/1", "1"),
    ],
)
def test_pr_number_reads_the_trailing_number(stub, url, want):
    r = stub.run(f'pr_number "{url}"\n')
    assert r.returncode == 0
    assert r.stdout.strip() == want


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://github.com/o/r/pull/",
        "https://github.com/o/r/issues/12",
        "not a url",
    ],
)
def test_pr_number_refuses_anything_that_is_not_a_pr_url(stub, url):
    """`pr_num="${pr_url##*/}"` on an empty url POSTed `issues//labels` -> 404."""
    r = stub.run(f'pr_number "{url}"\n')
    assert r.returncode != 0
    assert r.stdout.strip() == ""


# --- arm_pr -----------------------------------------------------------------

ARM = f'arm_pr szymonrychu/tatara-helmfile "{URL}" semver:patch'
ARMED = "0 2026-08-17T15:49:02Z"


def test_arms_and_confirms_by_reading_the_state_back(stub):
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0
    assert len(stub.calls("pr-merge")) == 1
    assert stub.calls("pr-view"), "must read autoMergeRequest back"


def test_refuses_when_auto_merge_reads_back_null(stub):
    """`gh pr merge --auto` exiting 0 is not evidence that it armed."""
    stub.plan("pr-view", ["0"])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0
    assert "auto-merge" in (r.stdout + r.stderr).lower()


def test_refuses_an_empty_url_before_touching_the_forge(stub):
    r = stub.run('arm_pr szymonrychu/tatara-helmfile "" semver:patch\n')
    assert r.returncode != 0
    assert stub.calls("api") == []
    assert stub.calls("pr-merge") == []


def test_a_label_post_that_never_succeeds_fails_the_step(stub):
    stub.plan("api", ["1", "1", "1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0
    assert stub.calls("pr-merge") == [], "must not arm a PR it could not label"


def test_a_transient_arm_failure_is_retried(stub):
    stub.plan("pr-merge", ["1", "0"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0
    assert len(stub.calls("pr-merge")) == 2


def test_an_arm_that_never_succeeds_fails_the_step(stub):
    stub.plan("pr-merge", ["1", "1", "1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0


def test_label_create_already_exists_is_not_fatal(stub):
    """The managed label usually exists; `gh label create` 1 is the normal case."""
    stub.plan("label-create", ["1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0


# --- retry ------------------------------------------------------------------


def test_retry_returns_zero_and_only_the_successful_attempts_stdout(stub):
    stub.plan("pr-view", ["1 garbage-from-a-failed-attempt", f"0 {URL}"])
    r = stub.run('retry probe gh pr view --json url\n')
    assert r.returncode == 0
    assert r.stdout.strip() == URL


def test_retry_gives_up_after_the_configured_attempts(stub):
    stub.plan("pr-view", ["1", "1", "1", "1", "1"])
    r = stub.run('retry probe gh pr view --json url || echo "GAVE_UP $?"\n')
    assert "GAVE_UP 1" in r.stdout
    assert len(stub.calls("pr-view")) == 3, "GH_RETRY_ATTEMPTS=3"


def test_retry_does_not_leak_the_command_it_runs_into_the_log(stub):
    """Labels are printed, argv is not: argv carries the token in git URLs."""
    stub.plan("pr-view", ["1", "1", "1"])
    r = stub.run(
        'retry "gh pr view" gh pr view --json url --secret hunter2 || true\n'
    )
    assert "hunter2" not in r.stdout + r.stderr


# --- mode=tag: the semver label read ----------------------------------------
#
# Same failure shape as the bump step, opposite blast radius: a `|| true` over
# the whole `gh pr list | grep | cut` pipeline turns a 503 into an empty
# `level`, and the step then reports "no semver:(major|minor|patch) label;
# refusing to tag" on a release that MUST be tagged. The block is scanned out
# of the action and run for real, the way test_cd_release_terminal_noop.py runs
# the no-op discriminator.

LABEL_READ_START = "# 1. find the merged PR"
LABEL_READ_END = 'echo "significance level: $level"'


def label_read_block():
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if LABEL_READ_START in ln]
    assert len(starts) == 1, f"expected one label read, found {len(starts)}"
    ends = [i for i, ln in enumerate(lines) if LABEL_READ_END in ln]
    assert len(ends) == 1, f"expected one level echo, found {len(ends)}"
    return textwrap.dedent("\n".join(lines[starts[0] : ends[0]]))


def read_level(stub, plan):
    stub.plan("pr-list", plan)
    return stub.run(
        'PY="$(command -v python3)"\nSHA=deadbeef\n' + label_read_block() + "\necho LEVEL=[$level]\n"
    )


def test_the_label_is_read_off_the_merged_pr(stub):
    payload = '[{"number":423,"labels":[{"name":"area/cd"},{"name":"semver:minor"}]}]'
    r = read_level(stub, [f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[minor]" in r.stdout


def test_a_forge_failure_is_not_reported_as_a_missing_label(stub):
    """The wrong diagnosis: a 503 must not read as "refusing to tag"."""
    r = read_level(stub, ["1", "1", "1", "1", "1"])
    assert r.returncode != 0
    assert "LEVEL=" not in r.stdout
    err = r.stdout + r.stderr
    assert "could not read" in err
    assert "no semver" not in err, "a transient forge error is not a missing label"


def test_a_genuinely_unlabelled_pr_still_refuses_to_tag(stub):
    """gh exits 0 with an empty label list; that IS the refusal case."""
    r = read_level(stub, ['0 [{"number":423,"labels":[]}]'])
    assert r.returncode != 0
    assert "no semver" in r.stdout + r.stderr


def test_a_transient_failure_before_a_good_read_is_retried(stub):
    payload = '[{"number":423,"labels":[{"name":"semver:major"}]}]'
    r = read_level(stub, ["1", f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[major]" in r.stdout


def test_a_lookalike_label_is_not_mistaken_for_a_semver_level(stub):
    """Parsed, not grepped: `not-semver:major` in the JSON is not a level."""
    payload = (
        '[{"number":423,"labels":[{"name":"not-semver:major"},{"name":"semver:patch"}]}]'
    )
    r = read_level(stub, [f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[patch]" in r.stdout


# --- action.yml wiring ------------------------------------------------------


def test_every_step_that_sets_errexit_also_inherits_it_into_substitutions():
    """`set -e` alone does not survive `x="$(f)"`. That is the whole defect."""
    text = ACTION.read_text(encoding="utf-8")
    assert text.count("set -euo pipefail") == text.count("shopt -s inherit_errexit")


def test_the_action_defines_no_forge_helpers_of_its_own():
    """One implementation, in the lib the tests cover. No drifting copy."""
    text = ACTION.read_text(encoding="utf-8")
    for helper in ("open_or_reuse_pr()", "arm_pr()", "ensure_label()"):
        assert helper not in text, f"{helper} must live in gh-lib.sh"
    assert 'source "$ACTION_PATH/gh-lib.sh"' in text


def test_the_action_never_swallows_a_forge_read_with_or_true():
    """`|| true` on a read is how a 503 became "no semver label; refusing to tag"."""
    for line in ACTION.read_text(encoding="utf-8").splitlines():
        if "|| true" in line and ("gh " in line or "git fetch" in line):
            raise AssertionError(f"forge read swallowed by `|| true`: {line.strip()}")
