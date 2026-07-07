#!/usr/bin/env python3
"""Rewrite parent-repo pins in place by regex (never by line number).

    python apply-pins.py <pins_json> <new_value>

`pins_json` is a JSON array of objects:

    [{"file": "...", "pattern": "<regex>", "value_template": "<repl>"}]

`value_template` is a `re.sub` replacement string. It may carry capture
backreferences (`\\1`) from `pattern` and the mustache placeholders:

    {{version}}            -> new_value, v-prefixed   (e.g. v1.4.0)  image tags
    {{image_version}}      -> new_value, v-prefixed   (alias of version)
    {{chart_version_bare}} -> new_value, v stripped   (e.g. 1.4.0)   chart pins

Every pin MUST match exactly once; a pin whose pattern matches nothing is a
hard error (the pin drifted) so the cascade fails loudly instead of silently
shipping a stale version. Rewrites are idempotent (re-running with the same
new_value is a no-op-equivalent rewrite, still count==1).
"""
import json
import re
import sys


def _substitute_mustache(template, new_value):
    v = new_value.strip()
    bare = v[1:] if v.startswith("v") else v
    vpref = v if v.startswith("v") else f"v{v}"
    return (
        template.replace("{{version}}", vpref)
        .replace("{{image_version}}", vpref)
        .replace("{{chart_version_bare}}", bare)
    )


def _normalize_backrefs(template):
    # Rewrite `\1`..`\9` to the unambiguous `\g<1>` form so a version that
    # starts with a digit (e.g. `\1` + `1.5.0` -> `\11.5.0`) is not misread by
    # re.sub as backreference group 11. Leaves existing `\g<N>` untouched.
    return re.sub(r"\\([1-9])", r"\\g<\1>", template)


def apply_pin(content, pattern, value_template, new_value):
    repl = _substitute_mustache(_normalize_backrefs(value_template), new_value)
    new_content, count = re.subn(pattern, repl, content, flags=re.MULTILINE)
    if count == 0:
        raise ValueError(f"pin pattern matched nothing: {pattern!r}")
    return new_content


def apply_pins(pins, new_value, read_file, write_file):
    """Apply every pin; return the list of files that changed."""
    changed = []
    for pin in pins:
        path = pin["file"]
        content = read_file(path)
        new_content = apply_pin(content, pin["pattern"], pin["value_template"], new_value)
        if new_content != content:
            write_file(path, new_content)
            changed.append(path)
    return changed


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: apply-pins.py <pins_json> <new_value>\n")
        raise SystemExit(2)
    pins_json, new_value = argv
    pins = json.loads(pins_json)

    def read_file(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_file(path, data):
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)

    try:
        changed = apply_pins(pins, new_value, read_file, write_file)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        raise SystemExit(1)
    for p in changed:
        print(p)


if __name__ == "__main__":
    main(sys.argv[1:])
