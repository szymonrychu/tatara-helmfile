#!/usr/bin/env python3
"""Rewrite parent-repo pins in place by regex (never by line number).

    python apply-pins.py <pins_json> <new_value>

`pins_json` is a JSON array of objects:

    [{"file": "...", "pattern": "<regex>", "value_template": "<repl>",
      "expect": 1}]

`value_template` is a `re.sub` replacement string. It may carry capture
backreferences (`\\1`) from `pattern` and the mustache placeholders:

    {{version}}            -> new_value, v-prefixed   (e.g. v1.4.0)  image tags
    {{image_version}}      -> new_value, v-prefixed   (alias of version)
    {{chart_version_bare}} -> new_value, v stripped   (e.g. 1.4.0)   chart pins

Every pin MUST match exactly once, and BOTH directions are hard errors: a
pattern that matches nothing means the pin drifted, and a pattern that matches
more than once means it is about to rewrite a site nobody declared. Either way
the cascade fails loudly instead of silently shipping a stale version or a
wrong one. Rewrites are idempotent (re-running with the same new_value is a
no-op-equivalent rewrite, still count==1).

A pin that LEGITIMATELY targets several sites in one file opts in with an
explicit `"expect": N` (integer >= 1, default 1), so the fan-in is declared in
the producer's `pins:` array and shows up in review rather than being absorbed
by deleting the arity check.
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


SITES_SHOWN = 10


def _match_sites(content, pattern):
    """`line N: <text>` for every line a match starts on, capped."""
    lines = content.split("\n")
    sites = []
    for m in re.finditer(pattern, content, flags=re.MULTILINE):
        n = content.count("\n", 0, m.start()) + 1
        sites.append(f"    line {n}: {lines[n - 1]}")
    if len(sites) > SITES_SHOWN:
        extra = len(sites) - SITES_SHOWN
        sites = sites[:SITES_SHOWN] + [f"    ... and {extra} more"]
    return sites


def _arity_error(content, pattern, path, expect, count):
    where = f" in {path}" if path else ""
    if count == 0:
        return (
            f"pin pattern matched nothing{where} (expected {expect}): {pattern!r}. "
            "The pin drifted: the site was renamed, reformatted or removed."
        )
    # count>1 (or a short count under an `expect` opt-in): the pattern is not
    # the whole story, so name the sites it was about to rewrite.
    head = (
        f"pin pattern matched {count} times{where}, expected {expect}: {pattern!r}. "
        "Narrow the pattern, or declare the fan-in with \"expect\": "
        f"{count} in the producer's pins array."
    )
    return "\n".join([head] + _match_sites(content, pattern))


def apply_pin(content, pattern, value_template, new_value, path=None, expect=1):
    repl = _substitute_mustache(_normalize_backrefs(value_template), new_value)
    new_content, count = re.subn(pattern, repl, content, flags=re.MULTILINE)
    if count != expect:
        raise ValueError(_arity_error(content, pattern, path, expect, count))
    return new_content


def _pin_expect(pin, path):
    """The pin's declared arity. Anything but a positive int is the pin's bug.

    bool is an int subclass, so `"expect": true` would otherwise pass as 1 and
    a JSON typo would quietly re-acquire the default it meant to override.
    """
    expect = pin.get("expect", 1)
    if isinstance(expect, bool) or not isinstance(expect, int) or expect < 1:
        raise ValueError(
            f'pin for {path}: "expect" must be an integer >= 1, got {expect!r}'
        )
    return expect


def apply_pins(pins, new_value, read_file, write_file):
    """Apply every pin; return the list of files that changed."""
    changed = []
    for pin in pins:
        path = pin["file"]
        content = read_file(path)
        new_content = apply_pin(
            content,
            pin["pattern"],
            pin["value_template"],
            new_value,
            path=path,
            expect=_pin_expect(pin, path),
        )
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
