import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "semver-bump.py"

# The script ships as `semver-bump.py` (hyphen) to match the CI invocation
# `python semver-bump.py ...`; a hyphenated name is not importable, so load it
# by path for the unit tests of its pure functions.
_spec = importlib.util.spec_from_file_location("semver_bump", SCRIPT)
semver_bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(semver_bump)
bump = semver_bump.bump
main = semver_bump.main


@pytest.mark.parametrize(
    "latest,level,expected",
    [
        # patch increments Z
        ("v1.4.2", "patch", "v1.4.3"),
        ("v0.0.0", "patch", "v0.0.1"),
        ("v2.7.9", "patch", "v2.7.10"),
        # minor bumps Y, resets Z
        ("v1.4.2", "minor", "v1.5.0"),
        ("v0.0.9", "minor", "v0.1.0"),
        ("v2.0.0", "minor", "v2.1.0"),
        # major bumps X, resets Y and Z
        ("v1.4.2", "major", "v2.0.0"),
        ("v0.9.9", "major", "v1.0.0"),
        ("v3.0.0", "major", "v4.0.0"),
    ],
)
def test_bump_table(latest, level, expected):
    assert bump(latest, level) == expected


def test_seed_empty_string_patch():
    # No prior semver -> start from v0.0.0, patch => v0.0.1
    assert bump("", "patch") == "v0.0.1"


def test_seed_empty_string_minor():
    assert bump("", "minor") == "v0.1.0"


def test_seed_empty_string_major():
    assert bump("", "major") == "v1.0.0"


def test_seed_non_semver_tag_treated_as_seed():
    # A bare short-SHA tag (no semver) is treated as the v0.0.0 seed.
    assert bump("abc1234", "patch") == "v0.0.1"


def test_bare_semver_no_v_prefix_gains_v():
    # Input without a leading v still produces a v-prefixed output.
    assert bump("1.2.3", "patch") == "v1.2.4"


def test_leading_v_preserved():
    assert bump("v1.2.3", "patch").startswith("v")


def test_invalid_level_raises():
    with pytest.raises(ValueError):
        bump("v1.0.0", "bogus")


def test_whitespace_tag_is_seed():
    assert bump("  \n", "patch") == "v0.0.1"


def test_cli_entrypoint_prints_next():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "v1.4.2", "minor"],
        text=True,
    ).strip()
    assert out == "v1.5.0"


def test_cli_entrypoint_seed():
    out = subprocess.check_output(
        [sys.executable, str(SCRIPT), "", "patch"],
        text=True,
    ).strip()
    assert out == "v0.0.1"


def test_main_bad_args_exits_nonzero():
    with pytest.raises(SystemExit):
        main(["only-one-arg"])
