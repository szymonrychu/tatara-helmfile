"""Exit-status propagation through apply.yaml's enrollment step (#398).

The `reconcile enrollment CRs` step is the half of the fix that actually
reconciles the `Project`/`Repository` CRs, and it is a second, independent
implementation of the same loop `.hook.sh` uses. Nothing pinned it, so the two
could drift and only one would be caught.

The step's `run:` block is EXTRACTED FROM THE COMMITTED WORKFLOW - not a copy -
and executed under `bash --noprofile --norc -eo pipefail` (what `shell: bash`
resolves to) against a synthetic values tree and the same fake kubectl/sops the
hook suite uses. No cluster, no secrets: hosted-runner safe.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from fakes import install_fakes

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "apply.yaml"

STEP_NAME = "reconcile enrollment CRs"
RAW = "values/tatara-operator/raw"

STEP_RE = re.compile(r"^(\s*)- name: .*%s" % re.escape(STEP_NAME))


def _step_lines():
    lines = WORKFLOW.read_text().splitlines()
    for i, line in enumerate(lines):
        if STEP_RE.match(line):
            indent = len(line) - len(line.lstrip()) + 2
            body = [line]
            for nxt in lines[i + 1 :]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) < indent:
                    break
                body.append(nxt)
            return body, indent
    raise AssertionError("step %r not found in %s" % (STEP_NAME, WORKFLOW))


def enrollment_script() -> str:
    """The step's `run: |` block, dedented, exactly as committed."""
    body, indent = _step_lines()
    for n, line in enumerate(body):
        if line.strip() == "run: |":
            block = []
            for nxt in body[n + 1 :]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                block.append(nxt[indent + 2 :] if nxt.strip() else "")
            assert block, "empty run block"
            return "\n".join(block) + "\n"
    raise AssertionError("no `run: |` in step %r" % STEP_NAME)


def test_the_step_declares_shell_bash():
    """`shell: bash` is load-bearing, not cosmetic: the default `run:` shell is
    `bash -e` with NO pipefail, under which `sops -d | kubectl apply` takes its
    status from kubectl alone and a failed decrypt applies nothing and reports
    success. These tests run the block under pipefail, so without this
    assertion they would pass while CI ran it without."""
    body, _ = _step_lines()
    assert any(line.strip() == "shell: bash" for line in body), body


def run_enrollment(
    tmp_path: Path,
    *,
    plain=(),
    secrets=(),
    kubectl_rc=None,
    sops_rc=None,
    unreadable=(),
    find_rc=None,
    remove_raw=False,
):
    root = tmp_path / "repo"
    raw = root / RAW
    if not remove_raw:
        raw.mkdir(parents=True)
        for name in plain + secrets:
            (raw / name).parent.mkdir(parents=True, exist_ok=True)
            (raw / name).write_text("# manifest\n")
    else:
        root.mkdir(parents=True)

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    mise = home / ".local" / "bin" / "mise"
    mise.write_text("#!/bin/sh\n")  # `mise activate bash` emits nothing
    mise.chmod(0o755)

    script = root / "step.sh"
    script.write_text(enrollment_script())

    log = tmp_path / "attempts.log"
    log.write_text("")

    env = dict(os.environ)
    fakebin = install_fakes(tmp_path, fake_find=find_rc is not None)
    env["PATH"] = "%s:%s" % (fakebin, env["PATH"])
    env["HOME"] = str(home)
    env["FAKE_LOG"] = str(log)
    env["FAKE_RC"] = json.dumps(kubectl_rc or {})
    env["SOPS_RC"] = json.dumps(sops_rc or {})
    env["FIND_RC"] = str(find_rc or 0)

    locked = [root / rel for rel in unreadable]
    for d in locked:
        d.chmod(0o000)
    try:
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        for d in locked:
            d.chmod(0o755)
    return proc, log.read_text().splitlines()


def test_clean_enrollment_succeeds(tmp_path):
    proc, attempts = run_enrollment(
        tmp_path,
        plain=("project.yaml", "repos.yaml"),
        secrets=("scm.secrets.yaml",),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len([a for a in attempts if a.startswith("kubectl ")]) == 3


def test_invoked_commands_get_devnull_on_stdin(tmp_path):
    proc, attempts = run_enrollment(
        tmp_path, plain=("project.yaml",), secrets=("scm.secrets.yaml",)
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    noted = [a for a in attempts if "stdin=" in a]
    assert len(noted) == 2, attempts  # the kubectl on the sops pipe is exempt
    for line in noted:
        assert line.endswith("stdin=/dev/null"), line


def test_a_rejected_manifest_fails_the_step(tmp_path):
    plain = ("a.yaml", "b.yaml", "c.yaml")
    proc, attempts = run_enrollment(
        tmp_path, plain=plain, kubectl_rc={"b.yaml": 1}
    )
    assert proc.returncode != 0
    for name in plain:
        assert any(name in a for a in attempts), (name, attempts)


def test_a_failed_decrypt_fails_the_step(tmp_path):
    """`sops -d` fails, kubectl still exits 0: masked without pipefail."""
    proc, _ = run_enrollment(
        tmp_path,
        secrets=("scm.secrets.yaml",),
        sops_rc={"scm.secrets.yaml": 1},
    )
    assert proc.returncode != 0


def test_a_find_traversal_failure_fails_the_step(tmp_path):
    """Holds for any euid, unlike the 0000-mode case below."""
    proc, attempts = run_enrollment(tmp_path, plain=("project.yaml",), find_rc=1)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert any("project.yaml" in a for a in attempts), attempts


def test_a_missing_raw_tree_fails_the_step(tmp_path):
    """Unlike `.hook.sh`, this block has no `[[ -d ]]` guard: a values tree
    reorganised out from under it enrolls nothing, and used to report success.
    """
    proc, attempts = run_enrollment(tmp_path, remove_raw=True)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert attempts == [], attempts


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores the 0000 mode that makes find fail"
)
def test_an_unreadable_raw_tree_fails_the_step(tmp_path):
    """find's own traversal status is discarded by a process substitution.

    Nothing is enrolled and the step whose comment promises deterministic
    enrollment reports success - the #398 swallow in a new place.
    """
    proc, attempts = run_enrollment(
        tmp_path, plain=("project.yaml",), unreadable=(RAW,)
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert attempts == [], attempts
