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

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".hook.sh"

RELEASE = "demo"
NAMESPACE = "demo-ns"

FAKE_KUBECTL = '''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]

# The presync health check probes the namespace first. Report it missing so the
# `helm list` wait loop is skipped: it is not what these tests are about.
if args[:2] == ["get", "namespace"]:
    sys.exit(1)

if "-f" in args:
    target = args[args.index("-f") + 1]
else:
    target = "<none>"

if target == "-":
    payload = sys.stdin.read().strip()
    # The fake sops emits "decrypted:<basename>"; an empty read means the
    # decrypt produced nothing.
    name = payload.split("decrypted:")[-1] if payload else "<empty-stdin>"
else:
    name = os.path.basename(target)

with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write("kubectl %s %s\\n" % (args[0], name))

sys.exit(json.loads(os.environ.get("FAKE_RC", "{}")).get(name, 0))
'''

FAKE_SOPS = '''#!/usr/bin/env python3
import json, os, sys

name = os.path.basename(sys.argv[-1])
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write("sops %s\\n" % name)

rc = json.loads(os.environ.get("SOPS_RC", "{}")).get(name, 0)
if rc:
    sys.exit(rc)
print("decrypted:%s" % name)
'''

FAKE_HELM = '''#!/usr/bin/env python3
print("[]")
'''


def _bin(tmp_path: Path) -> Path:
    binf = tmp_path / "fakebin"
    binf.mkdir()
    for name, body in (
        ("kubectl", FAKE_KUBECTL),
        ("sops", FAKE_SOPS),
        ("helm", FAKE_HELM),
    ):
        p = binf / name
        p.write_text(body)
        p.chmod(0o755)
    return binf


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
    global_files=(),
    kubectl_rc=None,
    sops_rc=None,
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
    for name in global_files:
        _write(root / "raw" / name)

    log = tmp_path / "attempts.log"
    log.write_text("")

    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (_bin(tmp_path), env["PATH"])
    env["FAKE_LOG"] = str(log)
    env["FAKE_RC"] = json.dumps(kubectl_rc or {})
    env["SOPS_RC"] = json.dumps(sops_rc or {})

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
