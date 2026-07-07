#!/usr/bin/env python3
"""Compute the next semver tag from the latest tag and a bump level.

Pure function, no git/network. Invoked by the cd-release composite action:

    python semver-bump.py <latest_tag> <level>

prints the next v-prefixed tag (e.g. v1.4.0) to stdout. `level` is one of
major|minor|patch. A missing/non-semver `latest_tag` is treated as the v0.0.0
seed, so a fresh repo's first patch is v0.0.1.
"""
import re
import sys

_LEVELS = ("major", "minor", "patch")
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse(latest_tag):
    """Return (major, minor, patch) from a tag, or (0, 0, 0) for a seed."""
    m = _SEMVER.match((latest_tag or "").strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(g) for g in m.groups())


def bump(latest_tag, level):
    if level not in _LEVELS:
        raise ValueError(f"level must be one of {'|'.join(_LEVELS)}, got {level!r}")
    major, minor, patch = _parse(latest_tag)
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: semver-bump.py <latest_tag> <level>\n")
        raise SystemExit(2)
    latest_tag, level = argv
    try:
        print(bump(latest_tag, level))
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
