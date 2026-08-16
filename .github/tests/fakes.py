"""Fake `kubectl`/`sops`/`helm` shared by the exit-status suites.

Each logs every invocation to $FAKE_LOG and exits with a per-file code looked up
in $FAKE_RC / $SOPS_RC (JSON objects keyed by basename), so a test can say "this
one manifest is rejected" and then assert on both the exit status and the full
attempt list.
"""

from pathlib import Path

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
    note = ""
else:
    name = os.path.basename(target)
    # Recorded, never read: reading it is what the loop must be protected FROM.
    # `< /dev/null` shows up as a readlink to /dev/null, the inherited NUL file
    # list as a pipe.
    try:
        note = " stdin=%s" % os.readlink("/proc/self/fd/0")
    except OSError:
        note = " stdin=unknown"

with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write("kubectl %s %s%s\\n" % (args[0], name, note))

sys.exit(json.loads(os.environ.get("FAKE_RC", "{}")).get(name, 0))
'''

FAKE_SOPS = '''#!/usr/bin/env python3
import json, os, sys

name = os.path.basename(sys.argv[-1])
try:
    note = " stdin=%s" % os.readlink("/proc/self/fd/0")
except OSError:
    note = " stdin=unknown"
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write("sops %s%s\\n" % (name, note))

rc = json.loads(os.environ.get("SOPS_RC", "{}")).get(name, 0)
if rc:
    sys.exit(rc)
print("decrypted:%s" % name)
'''

FAKE_HELM = '''#!/usr/bin/env python3
print("[]")
'''

# Delegates to the real find, then overrides a clean exit with $FIND_RC. An
# unreadable subtree is the faithful way to make find fail, but root ignores
# mode 0000, so those cases have to skip - and they are exactly the cases that
# pin `wait "$!"`. This makes the same pin hold for any euid.
FAKE_FIND = '''#!/usr/bin/env python3
import os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
real = None
for d in os.environ.get("PATH", "").split(os.pathsep):
    if os.path.abspath(d or ".") == here:
        continue
    cand = os.path.join(d, "find")
    if os.access(cand, os.X_OK):
        real = cand
        break
if real is None:
    sys.exit("fake find: no real find on PATH")

rc = subprocess.run([real] + sys.argv[1:]).returncode
sys.exit(rc or int(os.environ.get("FIND_RC", "0")))
'''


def install_fakes(tmp_path: Path, *, fake_find: bool = False) -> Path:
    """Write the fakes into <tmp_path>/fakebin and return that directory."""
    binf = tmp_path / "fakebin"
    binf.mkdir()
    fakes = [("kubectl", FAKE_KUBECTL), ("sops", FAKE_SOPS), ("helm", FAKE_HELM)]
    if fake_find:
        fakes.append(("find", FAKE_FIND))
    for name, body in fakes:
        p = binf / name
        p.write_text(body)
        p.chmod(0o755)
    return binf
