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


def install_fakes(tmp_path: Path) -> Path:
    """Write the fakes into <tmp_path>/fakebin and return that directory."""
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
