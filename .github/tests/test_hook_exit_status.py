"""Exit-status propagation through .hook.sh (#398).

`find -exec cmd \\;` reports find's OWN traversal status, never cmd's, so every
rejected manifest applied by the presync/postsync hook used to report success.
These tests pin the contract that replaced it:

  - every match is attempted, even after one fails (no first-failure abort)
  - a real failure makes the whole hook exit non-zero
  - under `prepare` (where KUBECTL is `kubectl diff`) exit 1 means "differences
    found" and is NOT a failure; anything else still is
  - a hook script has no diff-exit convention: any non-zero is a failure, under
    `prepare` too
  - find's OWN traversal status (an unreadable subtree) is still fatal: the
    process substitution the loop reads from discards it unless waited on
  - the loop body must not inherit the NUL file list as stdin, or a hook script
    that reads stdin drains the remaining matches and the loop stops early

The hook is exercised as a black box: it is copied into a tmp tree next to a
synthetic values/ layout, with fake `kubectl`/`sops`/`helm` on PATH that log
every invocation and exit with a per-file code from FAKE_RC/SOPS_RC.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fakes import install_fakes

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".hook.sh"

RELEASE = "demo"
NAMESPACE = "demo-ns"


def _write(path: Path, body: str = "# manifest\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    if path.suffix == ".sh":
        path.chmod(0o755)


def run_hook(
    tmp_path: Path,
    event: str,
    *,
    release_files=(),
    hook_files=(),
    hook_scripts=(),
    global_files=(),
    kubectl_rc=None,
    sops_rc=None,
    unreadable=(),
    find_rc=None,
):
    """Copy .hook.sh into a synthetic tree, run it, return (rc, attempt log)."""
    root = tmp_path / "helmfile"
    root.mkdir()
    shutil.copy(HOOK, root / ".hook.sh")
    (root / ".hook.sh").chmod(0o755)

    for name in release_files:
        _write(root / "values" / RELEASE / "raw" / name)
    for name, rc in hook_files:
        _write(
            root / "values" / RELEASE / "hooks" / name,
            "#!/bin/bash\nexit %d\n" % rc,
        )
    for name, body in hook_scripts:
        _write(root / "values" / RELEASE / "hooks" / name, body)
    for name in global_files:
        _write(root / "raw" / name)

    log = tmp_path / "attempts.log"
    log.write_text("")

    env = dict(os.environ)
    fakebin = install_fakes(tmp_path, fake_find=find_rc is not None)
    env["PATH"] = "%s:%s" % (fakebin, env["PATH"])
    env["FAKE_LOG"] = str(log)
    env["FAKE_RC"] = json.dumps(kubectl_rc or {})
    env["SOPS_RC"] = json.dumps(sops_rc or {})
    env["FIND_RC"] = str(find_rc or 0)

    # Made unreadable AFTER the tree is populated and restored in `finally`, or
    # pytest's tmp_path reaper trips over it.
    locked = [root / rel for rel in unreadable]
    for d in locked:
        d.chmod(0o000)
    try:
        proc = subprocess.run(
            [
                "bash",
                str(root / ".hook.sh"),
                str(root),
                event,
                RELEASE,
                NAMESPACE,
                "1.0.0",
            ],
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


# --- the swallow itself ------------------------------------------------------


@pytest.mark.parametrize("event", ["presync", "postsync"])
def test_clean_run_succeeds(tmp_path, event):
    suff = "pre" if event == "presync" else "post"
    proc, attempts = run_hook(
        tmp_path,
        event,
        release_files=[
            "a.common.%s.yaml" % suff,
            "b.%s.%s.yaml" % (RELEASE, suff),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert len(attempts) == 2


def test_failed_apply_fails_the_hook(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        release_files=["a.%s.pre.yaml" % RELEASE],
        kubectl_rc={"a.%s.pre.yaml" % RELEASE: 1},
    )
    assert proc.returncode != 0


def test_every_match_is_attempted_even_after_a_failure(tmp_path):
    files = ["a.%s.pre.yaml" % RELEASE, "b.%s.pre.yaml" % RELEASE, "c.%s.pre.yaml" % RELEASE]
    proc, attempts = run_hook(
        tmp_path,
        "presync",
        release_files=files,
        kubectl_rc={files[1]: 1},
    )
    assert proc.returncode != 0
    # Attempt-everything-then-report: a first-failure abort would learn about
    # one broken manifest per apply run.
    for f in files:
        assert any(f in line for line in attempts), (f, attempts)


def test_failed_sops_decrypt_fails_the_hook(tmp_path):
    name = "s.%s.pre.secrets.yaml" % RELEASE
    proc, _ = run_hook(
        tmp_path,
        "presync",
        release_files=[name],
        sops_rc={name: 1},
    )
    assert proc.returncode != 0


def test_postsync_failure_is_fatal(tmp_path):
    name = "p.%s.post.yaml" % RELEASE
    proc, _ = run_hook(
        tmp_path,
        "postsync",
        release_files=[name],
        kubectl_rc={name: 1},
    )
    assert proc.returncode != 0


def test_global_raw_tree_failure_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        global_files=["g.pre.yaml"],
        kubectl_rc={"g.pre.yaml": 1},
    )
    assert proc.returncode != 0


def test_global_raw_secrets_decrypt_failure_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        global_files=["g.pre.secrets.yaml"],
        sops_rc={"g.pre.secrets.yaml": 1},
    )
    assert proc.returncode != 0


# --- the prepare/kubectl-diff carve-out --------------------------------------


def test_prepare_treats_diff_exit_1_as_differences_found(tmp_path):
    """The trap: `kubectl diff` exits 1 when there IS a difference.

    Propagating that uniformly would red every PR that changes a raw manifest.
    """
    files = ["a.common.pre.yaml", "b.%s.pre.yaml" % RELEASE]
    proc, attempts = run_hook(
        tmp_path,
        "prepare",
        release_files=files,
        kubectl_rc={f: 1 for f in files},
    )
    assert proc.returncode == 0, proc.stderr
    assert len(attempts) == 2


def test_prepare_treats_diff_exit_2_as_a_failure(tmp_path):
    name = "a.%s.pre.yaml" % RELEASE
    proc, _ = run_hook(
        tmp_path,
        "prepare",
        release_files=[name],
        kubectl_rc={name: 2},
    )
    assert proc.returncode != 0


def test_prepare_does_not_hide_a_sops_failure_behind_diff_exit_1(tmp_path):
    """`sops -d f | kubectl diff -f -` under pipefail yields 1 when sops fails
    and kubectl succeeds - indistinguishable from "differences found" unless
    the two halves are classified separately."""
    name = "s.%s.pre.secrets.yaml" % RELEASE
    proc, _ = run_hook(
        tmp_path,
        "prepare",
        release_files=[name],
        sops_rc={name: 1},
    )
    assert proc.returncode != 0


# --- hook scripts ------------------------------------------------------------


@pytest.mark.parametrize("event", ["presync", "prepare"])
def test_failed_hook_script_is_fatal(tmp_path, event):
    """A hook script has no diff-exit convention, so exit 1 is a failure even
    under `prepare`."""
    proc, _ = run_hook(
        tmp_path,
        event,
        hook_files=[("h.%s.pre.sh" % RELEASE, 1)],
    )
    assert proc.returncode != 0


def test_hook_script_exit_3_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        hook_files=[("h.common.pre.sh", 3)],
    )
    assert proc.returncode != 0


def test_clean_hook_scripts_succeed(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        hook_files=[("h.common.pre.sh", 0), ("h.%s.pre.sh" % RELEASE, 0)],
    )
    assert proc.returncode == 0, proc.stderr


# --- find's own traversal status ---------------------------------------------

root_only = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores the 0000 mode that makes find fail"
)


def test_a_find_traversal_failure_is_fatal(tmp_path):
    """Same contract as the 0000-subtree cases below, but with find's status
    injected, so it holds for any euid - including a root container, where
    those cases skip and would otherwise leave `wait "$!"` unpinned."""
    proc, attempts = run_hook(
        tmp_path,
        "presync",
        release_files=["a.%s.pre.yaml" % RELEASE],
        find_rc=1,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert any("a.%s.pre.yaml" % RELEASE in line for line in attempts), attempts


def test_a_find_traversal_failure_in_hooks_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        hook_files=[("h.common.pre.sh", 0)],
        find_rc=1,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr


@root_only
def test_unreadable_subtree_under_release_raw_is_fatal(tmp_path):
    """`done < <(find ...)` discards find's exit status.

    An unreadable subtree is the ONE failure find's own exit code did report,
    and which the pre-#398 `set -e` did catch. Silently skipping the manifests
    under it and reporting success is the same swallow in a new place.
    """
    proc, attempts = run_hook(
        tmp_path,
        "presync",
        release_files=["a.%s.pre.yaml" % RELEASE, "locked/b.%s.pre.yaml" % RELEASE],
        unreadable=["values/%s/raw/locked" % RELEASE],
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    # The readable half is still applied: attempt everything, then report.
    assert any("a.%s.pre.yaml" % RELEASE in line for line in attempts), attempts


@root_only
def test_unreadable_subtree_under_hooks_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        hook_files=[("locked/h.common.pre.sh", 0)],
        unreadable=["values/%s/hooks/locked" % RELEASE],
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr


@root_only
def test_unreadable_subtree_under_global_raw_is_fatal(tmp_path):
    proc, _ = run_hook(
        tmp_path,
        "presync",
        global_files=["locked/g.pre.yaml"],
        unreadable=["raw/locked"],
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr


# --- the loop's stdin --------------------------------------------------------

DRAINS_STDIN = """#!/bin/bash
printf 'hookscript %s\\n' "$(basename "$0")" >> "$FAKE_LOG"
cat > /dev/null
"""


def test_a_hook_script_reading_stdin_does_not_truncate_the_loop(tmp_path):
    """The loop body inherits the NUL file list as stdin unless redirected.

    `values/*/hooks/*.sh` is the documented extension point and its payload is
    arbitrary user shell, so any read of stdin there eats the remaining matches
    and the loop stops early - still exit 0.
    """
    names = ["a.common.pre.sh", "b.common.pre.sh", "c.common.pre.sh"]
    proc, attempts = run_hook(
        tmp_path,
        "presync",
        hook_scripts=[(n, DRAINS_STDIN) for n in names],
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ran = [line for line in attempts if line.startswith("hookscript ")]
    assert len(ran) == len(names), ran


def test_invoked_commands_get_devnull_on_stdin(tmp_path):
    """The `< /dev/null` on kubectl/sops is defensive - neither reads stdin
    today - so assert the redirection directly rather than via a behaviour
    only `run_hooks` can exhibit."""
    proc, attempts = run_hook(
        tmp_path,
        "presync",
        release_files=[
            "a.%s.pre.yaml" % RELEASE,
            "s.%s.pre.secrets.yaml" % RELEASE,
        ],
    )
    assert proc.returncode == 0, proc.stderr
    # The kubectl reading the sops pipe is the one deliberate exception, and it
    # logs no stdin= note at all.
    noted = [line for line in attempts if "stdin=" in line]
    assert len(noted) == 2, attempts
    for line in noted:
        assert line.endswith("stdin=/dev/null"), line
