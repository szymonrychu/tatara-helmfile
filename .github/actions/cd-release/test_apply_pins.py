import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "apply-pins.py"
_spec = importlib.util.spec_from_file_location("apply_pins", SCRIPT)
apply_pins_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_pins_mod)
apply_pin = apply_pins_mod.apply_pin
apply_pins = apply_pins_mod.apply_pins
_substitute_mustache = apply_pins_mod._substitute_mustache


def test_mustache_version_v_prefixed():
    assert _substitute_mustache("tag:{{version}}", "v1.4.0") == "tag:v1.4.0"
    assert _substitute_mustache("tag:{{version}}", "1.4.0") == "tag:v1.4.0"


def test_mustache_chart_bare_strips_v():
    assert _substitute_mustache("version: {{chart_version_bare}}", "v1.4.0") == "version: 1.4.0"
    assert _substitute_mustache("version: {{chart_version_bare}}", "1.4.0") == "version: 1.4.0"


def test_mustache_image_version_alias():
    assert _substitute_mustache("{{image_version}}", "v2.0.0") == "v2.0.0"


# --- real pin patterns from the parentMap, against representative content ---


def test_memory_image_pin():
    content = 'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:02bbd15"\n'
    pattern = r'^memoryImage: ".*"$'
    tmpl = 'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:{{version}}"'
    out = apply_pin(content, pattern, tmpl, "v1.4.0")
    assert out == 'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:v1.4.0"\n'


def test_ingester_image_pin_does_not_touch_memory():
    content = (
        'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:02bbd15"\n'
        'ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:11912dd"\n'
    )
    pattern = r'^ingesterImage: ".*"$'
    tmpl = 'ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:{{version}}"'
    out = apply_pin(content, pattern, tmpl, "v0.2.0")
    assert "tatara-memory:02bbd15" in out  # untouched
    assert "tatara-memory-repo-ingester:v0.2.0" in out


def test_pin_matching_two_sites_raises():
    # re.subn rewrites EVERY match. A pattern broad enough to hit both image
    # keys must be a hard error, not a silent double rewrite.
    content = (
        'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:v1.4.0"\n'
        'ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:v0.2.0"\n'
    )
    with pytest.raises(ValueError) as excinfo:
        apply_pin(content, r'^\S*Image: ".*"$', 'x: "{{version}}"', "v9.9.9",
                  path="values/tatara-operator/default.yaml")
    msg = str(excinfo.value)
    assert "values/tatara-operator/default.yaml" in msg
    assert "matched 2" in msg
    # The sites themselves: for count>1 the operator needs to know WHICH lines
    # were about to be rewritten, which the pattern alone does not say.
    assert "line 1: memoryImage:" in msg
    assert "line 2: ingesterImage:" in msg


def test_pin_expect_two_accepts_two_sites():
    content = (
        'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:v1.4.0"\n'
        'ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:v0.2.0"\n'
    )
    out = apply_pin(content, r'^\S*Image: ".*"$', 'x: "{{version}}"', "v9.9.9", expect=2)
    assert out == 'x: "v9.9.9"\nx: "v9.9.9"\n'


def test_pin_expect_two_rejects_a_single_site():
    with pytest.raises(ValueError) as excinfo:
        apply_pin('memoryImage: "old"\n', r'^\S*Image: ".*"$', 'x: "{{version}}"',
                  "v9.9.9", expect=2)
    msg = str(excinfo.value)
    assert "expected 2" in msg
    # A declared fan-in that lost a site must NOT be told to lower `expect`:
    # that is green CI over a permanently unpinned site.
    assert "do NOT lower `expect`" in msg
    assert '"expect": 1' not in msg


def test_over_match_message_caps_the_listed_sites():
    content = "".join(f'x{i}Image: "old"\n' for i in range(25))
    with pytest.raises(ValueError) as excinfo:
        apply_pin(content, r'^\S*Image: ".*"$', 'x: "{{version}}"', "v9.9.9")
    msg = str(excinfo.value)
    assert msg.count("    line ") == 10
    assert "... and 15 more" in msg


def test_apply_pins_honours_the_expect_key():
    store = {"f": 'aImage: "old"\nbImage: "old"\n'}
    pins = [{
        "file": "f",
        "pattern": r'^\S*Image: ".*"$',
        "value_template": 'x: "{{version}}"',
        "expect": 2,
    }]
    changed = apply_pins(pins, "v9.9.9", lambda p: store[p], lambda p, d: store.__setitem__(p, d))
    assert changed == ["f"]
    assert store["f"] == 'x: "v9.9.9"\nx: "v9.9.9"\n'


def test_apply_pins_rejects_a_non_integer_expect():
    # A JSON typo (`"expect": "2"`) must not silently mean the default of 1.
    store = {"f": 'aImage: "old"\nbImage: "old"\n'}
    pins = [{
        "file": "f",
        "pattern": r'^\S*Image: ".*"$',
        "value_template": 'x: "{{version}}"',
        "expect": "2",
    }]
    with pytest.raises(ValueError) as excinfo:
        apply_pins(pins, "v9.9.9", lambda p: store[p], lambda p, d: store.__setitem__(p, d))
    assert "expect" in str(excinfo.value)


def test_apply_pins_rejects_a_boolean_expect():
    # bool is an int subclass in python; `"expect": true` must not mean 1.
    store = {"f": 'aImage: "old"\n'}
    pins = [{
        "file": "f",
        "pattern": r'^\S*Image: ".*"$',
        "value_template": 'x: "{{version}}"',
        "expect": True,
    }]
    with pytest.raises(ValueError):
        apply_pins(pins, "v9.9.9", lambda p: store[p], lambda p, d: store.__setitem__(p, d))


def test_pin_no_match_names_the_file():
    with pytest.raises(ValueError) as excinfo:
        apply_pin("nothing here\n", r"^DOES_NOT_EXIST=.*$", "x={{version}}", "v1.0.0",
                  path="values/some.yaml")
    assert "values/some.yaml" in str(excinfo.value)


def test_wrapper_image_pin_with_backreference():
    content = "      image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880\n"
    pattern = r"^(\s*image: )harbor\.szymonrichert\.pl/containers/tatara-claude-code-wrapper:.*$"
    tmpl = r"\1harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:{{version}}"
    out = apply_pin(content, pattern, tmpl, "v3.1.0")
    assert out == "      image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:v3.1.0\n"


def test_operator_chart_pin_matches_named_release_only():
    # Two releases share the same chart-version literal; the named-release regex
    # must touch ONLY the operator block, never tatara-chat's pin.
    content = (
        "- name: tatara-chat\n"
        "  chart: oci://harbor.szymonrichert.pl/charts/tatara-chat\n"
        "  namespace: tatara\n"
        "  version: 0.0.0-b050719\n"
        "- name: tatara-operator\n"
        "  chart: oci://harbor.szymonrichert.pl/charts/tatara-operator\n"
        "  namespace: tatara\n"
        "  version: 0.0.0-gfb8531a\n"
    )
    pattern = r"(- name: tatara-operator\n(?:.*\n)*?\s*version: )\S+"
    # Contract form uses `\1` (not `\g<1>`); chart_version_bare starts with a
    # digit, so this exercises the numeric-backref normalization.
    tmpl = r"\1{{chart_version_bare}}"
    out = apply_pin(content, pattern, tmpl, "v1.5.0")
    assert "  version: 0.0.0-b050719\n" in out  # chat untouched
    assert "- name: tatara-operator\n  chart: oci://harbor.szymonrichert.pl/charts/tatara-operator\n  namespace: tatara\n  version: 1.5.0\n" in out


def test_operator_image_tag_pin():
    content = "image:\n  repository: foo\n  tag: \"fb8531a\"\n"
    pattern = r'^(\s*tag: )".*"$'
    tmpl = r'\1"{{image_version}}"'
    out = apply_pin(content, pattern, tmpl, "v1.5.0")
    assert 'tag: "v1.5.0"' in out


def test_cli_dockerfile_arg_pin():
    content = "FROM alpine\nARG TATARA_CLI_VERSION=4593da4\nRUN true\n"
    pattern = r"^ARG TATARA_CLI_VERSION=.*$"
    tmpl = "ARG TATARA_CLI_VERSION={{version}}"
    out = apply_pin(content, pattern, tmpl, "v0.5.0")
    assert "ARG TATARA_CLI_VERSION=v0.5.0\n" in out


def test_cli_makefile_pin():
    content = "NAME = tatara-cli\nTATARA_CLI_VERSION ?= 7e62585\n"
    pattern = r"^TATARA_CLI_VERSION \?= .*$"
    tmpl = "TATARA_CLI_VERSION ?= {{version}}"
    out = apply_pin(content, pattern, tmpl, "v0.5.0")
    assert "TATARA_CLI_VERSION ?= v0.5.0\n" in out


def test_cli_buildsh_pin():
    content = 'set -e\nTATARA_CLI_VERSION="${TATARA_CLI_VERSION:-7e62585}"\n'
    pattern = r'^TATARA_CLI_VERSION="\$\{TATARA_CLI_VERSION:-[^}]*\}"$'
    tmpl = 'TATARA_CLI_VERSION="${TATARA_CLI_VERSION:-{{version}}}"'
    out = apply_pin(content, pattern, tmpl, "v0.5.0")
    assert 'TATARA_CLI_VERSION="${TATARA_CLI_VERSION:-v0.5.0}"\n' in out


def test_skills_dockerfile_arg_pin():
    content = "ARG TATARA_CLI_VERSION=v0.5.0\nARG TATARA_SKILLS_REF=v0.1.0\n"
    pattern = r"^ARG TATARA_SKILLS_REF=.*$"
    tmpl = "ARG TATARA_SKILLS_REF={{version}}"
    out = apply_pin(content, pattern, tmpl, "v0.2.0")
    assert "ARG TATARA_SKILLS_REF=v0.2.0\n" in out
    assert "ARG TATARA_CLI_VERSION=v0.5.0\n" in out  # untouched


def test_pin_no_match_raises():
    with pytest.raises(ValueError):
        apply_pin("nothing here\n", r"^DOES_NOT_EXIST=.*$", "DOES_NOT_EXIST={{version}}", "v1.0.0")


def test_apply_pins_idempotent_count():
    # Re-applying the same value still matches (count==1) and writes no diff.
    store = {"f": 'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:v1.4.0"\n'}
    pins = [{
        "file": "f",
        "pattern": r'^memoryImage: ".*"$',
        "value_template": 'memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:{{version}}"',
    }]
    changed = apply_pins(pins, "v1.4.0", lambda p: store[p], lambda p, d: store.__setitem__(p, d))
    assert changed == []  # already at target, no write


def test_apply_pins_multi_file_atomic_change_list():
    store = {
        "a": "ARG TATARA_CLI_VERSION=old\n",
        "b": "TATARA_CLI_VERSION ?= old\n",
    }
    pins = [
        {"file": "a", "pattern": r"^ARG TATARA_CLI_VERSION=.*$", "value_template": "ARG TATARA_CLI_VERSION={{version}}"},
        {"file": "b", "pattern": r"^TATARA_CLI_VERSION \?= .*$", "value_template": "TATARA_CLI_VERSION ?= {{version}}"},
    ]
    changed = apply_pins(pins, "v0.5.0", lambda p: store[p], lambda p, d: store.__setitem__(p, d))
    assert set(changed) == {"a", "b"}
    assert store["a"] == "ARG TATARA_CLI_VERSION=v0.5.0\n"
    assert store["b"] == "TATARA_CLI_VERSION ?= v0.5.0\n"
