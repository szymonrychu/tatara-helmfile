"""The terminal hop's no-op discriminator, extracted from the action and run.

`pushed="noop"` only says apply-pins found no diff against cd/deploy-train as it
stands. That is true both when the train has MERGED (nothing left to do) and
when the pin is already pushed but its PR was never armed - a prior run dying
between the push and `gh pr merge --auto`, or a push that landed server-side
while git reported failure and the retry loop then saw its own commit.

Exiting on both strands a pushed pin behind an unarmed PR, green, which is the
failure shape #397 exists to close. Falling through on both 422s `gh pr create`
once the train has merged, failing the calling job and skipping everything that
success-gates on it. So the branch-vs-main comparison is the discriminator, and
these tests run the real block against real git repos to prove it.
"""

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ACTION = Path(__file__).parents[1] / "actions" / "cd-release" / "action.yml"

FELL_THROUGH = "FELL_THROUGH"


def noop_block():
    """The `if [ "$pushed" = "noop" ]` block, verbatim, closing `fi` included.

    Scanned out of the raw file rather than parsed: `lint.yaml` installs pytest
    and nothing else, and every other suite here is stdlib-only. A YAML parse
    would be tidier and would cost this test its only runner.
    """
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r'\s*if \[ "\$pushed" = "noop" \]', ln)]
    assert len(starts) == 1, f"expected exactly one no-op guard, found {len(starts)}"
    start = starts[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "fi" and len(lines[i]) - len(lines[i].lstrip()) == indent:
            return textwrap.dedent("\n".join(lines[start : i + 1]))
    raise AssertionError("no-op guard is not closed")


def run_block(repo, block):
    """Run the block with pushed=noop; report whether it fell through."""
    script = f'set -euo pipefail\npushed="noop"\nbranch="cd/deploy-train"\n{block}\necho {FELL_THROUGH}\n'
    out = subprocess.run(
        ["bash", "-c", script], cwd=repo, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return FELL_THROUGH in out.stdout


def git(cwd, *argv):
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


class Clone:
    """A working clone plus the upstream it was cloned from."""

    def __init__(self, work, upstream):
        self.work = work
        self.upstream = upstream

    def advance_branch(self, branch):
        """Push a commit onto `branch` upstream, leaving main behind it."""
        git(self.upstream, "checkout", "-q", "-b", branch)
        (self.upstream / "pin").write_text("v2\n")
        git(self.upstream, "commit", "-qam", "cd: bump")
        git(self.upstream, "checkout", "-q", "main")
        git(self.work, "fetch", "-q", "origin")

    def level_branch(self, branch):
        """Point `branch` at main upstream: the merged-train state."""
        git(self.upstream, "branch", branch, "main")
        git(self.work, "fetch", "-q", "origin")


@pytest.fixture
def repo(tmp_path):
    """A clone whose origin/main exists; the caller adds origin/cd/deploy-train."""
    upstream, work = tmp_path / "upstream", tmp_path / "work"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    git(upstream, "config", "user.email", "t@t")
    git(upstream, "config", "user.name", "t")
    (upstream / "pin").write_text("v1\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "base")
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(work)], check=True, capture_output=True
    )
    return Clone(work, upstream)


def test_no_branch_at_all_exits_without_opening_a_pr(repo):
    """First bump of a value already on main: nothing to open, and no 422."""
    assert run_block(repo.work, noop_block()) is False


def test_branch_level_with_main_exits_without_opening_a_pr(repo):
    """The train merged. `gh pr create` here would 422 and fail the job."""
    repo.level_branch("cd/deploy-train")
    assert run_block(repo.work, noop_block()) is False


def test_branch_ahead_of_main_falls_through_so_the_pr_is_re_armed(repo):
    """Pin pushed, PR never armed. Exiting here strands it green."""
    repo.advance_branch("cd/deploy-train")
    assert run_block(repo.work, noop_block()) is True
