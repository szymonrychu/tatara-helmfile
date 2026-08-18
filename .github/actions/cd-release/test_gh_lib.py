"""gh-lib.sh: the forge helpers must fail LOUD, never quietly produce nothing.

Run 32035597231 attempt 4 (2026-08-17, GitHub partial outage): `gh pr list` 503
was eaten by `|| true`, `gh pr create` 503 next, and its failure did not
propagate - bash unsets errexit inside a command substitution unless
`inherit_errexit` is on, so `open_or_reuse_pr` printed an EMPTY url and returned
0. The pin was already pushed to cd/deploy-train; the job then POSTed
`issues//labels`, 404'd, and died leaving a pushed pin behind no PR and nothing
armed. Nothing could deploy until a human opened the PR.

So the contract these tests pin is narrow and absolute:

  - a forge read that FAILED is never reported as "no PR exists";
  - no helper ever prints an empty/partial url;
  - a failure reaches the CALLING script's exit status, through a command
    substitution;
  - the writer asserts auto-merge is armed by READING IT BACK, because
    `gh pr merge --auto` exiting 0 is not evidence.

`gh` is stubbed on PATH, scripted per subcommand, so the real bash runs.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).parent
LIB = HERE / "gh-lib.sh"
ACTION = HERE / "action.yml"

STUB_GH = """#!/usr/bin/env bash
# Scripted `gh`. Key is "$1" or "$1-$2" when $2 is not a flag: pr-list,
# pr-create, pr-view, pr-merge, label-create, api. Each key reads
# "$GH_STUB_DIR/<key>.plan": one line per invocation, "<exit> [stdout]".
# The last line repeats once the plan is exhausted.
key="$1"
if [ -n "${2-}" ] && [ "${2#-}" = "$2" ]; then key="$1-$2"; fi
printf '%s %s\\n' "$key" "$*" >> "$GH_STUB_DIR/calls.log"
plan="$GH_STUB_DIR/$key.plan"
n_file="$GH_STUB_DIR/$key.n"
n=$(( $(cat "$n_file" 2>/dev/null || echo 0) + 1 ))
printf '%s' "$n" > "$n_file"
[ -f "$plan" ] || exit 0
line="$(sed -n "${n}p" "$plan")"
[ -n "$line" ] || line="$(tail -n 1 "$plan")"
code="${line%% *}"
out="${line#* }"
[ "$out" = "$line" ] && out=""
[ -n "$out" ] && printf '%s\\n' "$out"
exit "$code"
"""

URL = "https://github.com/szymonrychu/tatara-helmfile/pull/423"


class Stub:
    def __init__(self, root):
        self.dir = root
        self.bin = root / "bin"
        self.bin.mkdir(parents=True)
        gh = self.bin / "gh"
        gh.write_text(STUB_GH, encoding="utf-8")
        gh.chmod(0o755)

    def plan(self, key, lines):
        (self.dir / f"{key}.plan").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def calls(self, key=None):
        log = self.dir / "calls.log"
        if not log.exists():
            return []
        got = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln]
        if key is None:
            return got
        return [ln for ln in got if ln.split(" ", 1)[0] == key]

    def stub_git(self):
        """Put a git on PATH that only records its argv, and log the call."""
        git = self.bin / "git"
        git.write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$GH_STUB_DIR/git.log"\n',
            encoding="utf-8",
        )
        git.chmod(0o755)

    def git_argv(self):
        log = self.dir / "git.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def run(self, snippet, env=None):
        """Run `snippet` in a script shaped exactly like an action.yml step."""
        script = (
            "set -euo pipefail\n"
            "shopt -s inherit_errexit\n"
            f'source "{LIB}"\n' + textwrap.dedent(snippet)
        )
        run_env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_STUB_DIR": str(self.dir),
            "GH_RETRY_ATTEMPTS": "3",
            "GH_RETRY_SLEEP": "0",
            **(env or {}),
        }
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=run_env,
            check=False,
        )


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path)


# --- open_or_reuse_pr -------------------------------------------------------

OPEN = f'open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train "cd: deploy-train" body'


def test_reuses_the_open_pr_without_creating_a_second(stub):
    stub.plan("pr-list", [f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert stub.calls("pr-create") == []


def test_creates_when_the_list_succeeded_and_found_nothing(stub):
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", [f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-create")) == 1


def test_a_failed_list_is_not_reported_as_no_pr_exists(stub):
    """The 2026-08-17 defect: `|| true` turned a 503 into "open a new one"."""
    stub.plan("pr-list", ["1", "1", "1"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode != 0
    assert r.stdout.strip() == ""
    assert stub.calls("pr-create") == [], "must not create a PR it could not rule out"


def test_a_transient_list_failure_is_retried(stub):
    stub.plan("pr-list", ["1", f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-list")) == 2


def test_a_transient_create_failure_is_retried(stub):
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", ["1", f"0 {URL}"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL
    assert len(stub.calls("pr-create")) == 2


@pytest.mark.parametrize(
    "name,list_plan,create_plan",
    [
        ("list down", ["1", "1", "1"], [f"0 {URL}"]),
        ("create down", ["0"], ["1", "1", "1"]),
        ("create returns nothing", ["0"], ["0"]),
        ("create returns a non-pr url", ["0"], ["0 https://github.com/o/r/issues/9"]),
        ("list returns a non-pr url", ["0 https://example.com/nope"], [f"0 {URL}"]),
    ],
)
def test_never_prints_an_empty_or_bogus_url(stub, name, list_plan, create_plan):
    """Whatever goes wrong, the caller gets a nonzero status and NO url."""
    stub.plan("pr-list", list_plan)
    stub.plan("pr-create", create_plan)
    r = stub.run(f"{OPEN}\n")
    assert r.returncode != 0, name
    assert r.stdout.strip() == "", name


def test_a_create_that_actually_landed_is_not_reported_as_no_pr(stub):
    """`gh pr create` is NOT idempotent.

    During a partial outage the POST can succeed server-side while the client
    sees a timeout, and every retry then gets `422 already exists`. Failing
    there reports "the PR does not exist" about a PR that does - the write-side
    mirror of the read-side defect this whole lib exists to close.
    """
    stub.plan("pr-list", ["0", f"0 {URL}"])
    stub.plan("pr-create", ["1", "1", "1"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode == 0
    assert r.stdout.strip() == URL


def test_a_create_that_failed_for_real_still_fails(stub):
    """The re-list must not paper over a create that genuinely opened nothing."""
    stub.plan("pr-list", ["0", "0", "0"])
    stub.plan("pr-create", ["1", "1", "1"])
    r = stub.run(f"{OPEN}\n")
    assert r.returncode != 0
    assert r.stdout.strip() == ""


# --- the caller contract ----------------------------------------------------


def test_failure_propagates_through_the_command_substitution(stub):
    """`pr_url="$(open_or_reuse_pr ...)" || exit 1` must stop the CALLING script.

    This is the half `set -e` does not give you: without `inherit_errexit` the
    substitution subshell runs with errexit off, so only an explicit nonzero
    return from the function stops the caller.
    """
    stub.plan("pr-list", ["0"])
    stub.plan("pr-create", ["1", "1", "1"])
    r = stub.run(
        """
        pr_url="$(open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train t b)" || exit 1
        echo "REACHED_ARM url=[$pr_url]"
        """
    )
    assert r.returncode != 0
    assert "REACHED_ARM" not in r.stdout, "armed a PR that was never opened"


def test_success_reaches_the_caller_intact(stub):
    stub.plan("pr-list", [f"0 {URL}"])
    r = stub.run(
        """
        pr_url="$(open_or_reuse_pr szymonrychu/tatara-helmfile cd/deploy-train t b)" || exit 1
        echo "REACHED_ARM url=[$pr_url]"
        """
    )
    assert r.returncode == 0
    assert f"REACHED_ARM url=[{URL}]" in r.stdout


# --- pr_number --------------------------------------------------------------


@pytest.mark.parametrize(
    "url,want",
    [
        (URL, "423"),
        ("https://github.com/o/r/pull/1", "1"),
    ],
)
def test_pr_number_reads_the_trailing_number(stub, url, want):
    r = stub.run(f'pr_number "{url}"\n')
    assert r.returncode == 0
    assert r.stdout.strip() == want


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://github.com/o/r/pull/",
        "https://github.com/o/r/issues/12",
        "not a url",
    ],
)
def test_pr_number_refuses_anything_that_is_not_a_pr_url(stub, url):
    """`pr_num="${pr_url##*/}"` on an empty url POSTed `issues//labels` -> 404."""
    r = stub.run(f'pr_number "{url}"\n')
    assert r.returncode != 0
    assert r.stdout.strip() == ""


# --- arm_pr -----------------------------------------------------------------

ARM = f'arm_pr szymonrychu/tatara-helmfile "{URL}" semver:patch'
ARMED = "0 ARMED"


def test_arms_and_confirms_by_reading_the_state_back(stub):
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0
    assert len(stub.calls("pr-merge")) == 1
    assert stub.calls("pr-view"), "must read autoMergeRequest back"


def test_refuses_when_auto_merge_reads_back_null(stub):
    """`gh pr merge --auto` exiting 0 is not evidence that it armed."""
    stub.plan("pr-view", ["0"])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0
    assert "auto-merge" in (r.stdout + r.stderr).lower()


def test_refuses_an_empty_url_before_touching_the_forge(stub):
    r = stub.run('arm_pr szymonrychu/tatara-helmfile "" semver:patch\n')
    assert r.returncode != 0
    assert stub.calls("api") == []
    assert stub.calls("pr-merge") == []


def test_a_label_post_that_never_succeeds_fails_the_step(stub):
    stub.plan("api", ["1", "1", "1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0
    assert stub.calls("pr-merge") == [], "must not arm a PR it could not label"


def test_a_transient_arm_failure_is_retried(stub):
    stub.plan("pr-merge", ["1", "0"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0
    assert len(stub.calls("pr-merge")) == 2


def test_an_arm_that_never_succeeds_fails_the_step(stub):
    stub.plan("pr-merge", ["1", "1", "1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode != 0


def test_a_pr_that_merged_mid_run_is_a_success_not_a_stranded_pin(stub):
    """cd/deploy-train is shared by six repos releasing concurrently.

    The pin this run just pushed is often what turns the helmfile diff green,
    so the PR can merge while this step is still arming it. GitHub then CLEARS
    autoMergeRequest, and reading it back reports "auto-merge is NOT armed; it
    would sit there forever" about a PR that has already merged - failing the
    release on the success path, and skipping verify-pin behind it.
    """
    stub.plan("pr-merge", ["1"])
    stub.plan("pr-view", ["0 MERGED"])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0
    assert "merged" in (r.stdout + r.stderr).lower()



def test_label_create_already_exists_is_not_fatal(stub):
    """The managed label usually exists; `gh label create` 1 is the normal case."""
    stub.plan("label-create", ["1"])
    stub.plan("pr-view", [ARMED])
    r = stub.run(f"{ARM}\n")
    assert r.returncode == 0


# --- retry ------------------------------------------------------------------


def test_retry_returns_zero_and_only_the_successful_attempts_stdout(stub):
    stub.plan("pr-view", ["1 garbage-from-a-failed-attempt", f"0 {URL}"])
    r = stub.run('retry probe gh pr view --json url\n')
    assert r.returncode == 0
    assert r.stdout.strip() == URL


def test_retry_gives_up_after_the_configured_attempts(stub):
    stub.plan("pr-view", ["1", "1", "1", "1", "1"])
    r = stub.run('retry probe gh pr view --json url || echo "GAVE_UP $?"\n')
    assert "GAVE_UP 1" in r.stdout
    assert len(stub.calls("pr-view")) == 3, "GH_RETRY_ATTEMPTS=3"


def test_retry_does_not_leak_the_command_it_runs_into_the_log(stub):
    """Labels are printed, argv is not.

    Nothing this wraps carries the credential any more - it comes from a
    credential helper, not from the URL - but the rule stands, because argv is
    exactly where the token reappears the moment anyone puts basic-auth
    userinfo back into a remote.
    """
    stub.plan("pr-view", ["1", "1", "1"])
    r = stub.run(
        'retry "gh pr view" gh pr view --json url --marker ARGV-MUST-NOT-APPEAR || true\n'
    )
    assert "ARGV-MUST-NOT-APPEAR" not in r.stdout + r.stderr


# --- git credentials --------------------------------------------------------
#
# The bot PAT must never appear in a URL. Carrying it as basic-auth userinfo in
# front of the host is what GitGuardian blocks on this repo, and it was right
# to: a credential in a remote URL is written into `.git/config`, echoed by
# `git remote -v`, copied into reflogs, printed in git's own error output, and
# carried in argv for the `git push <url>` form. Assembling the same URL from
# parts hid it from nothing. The credential is supplied out of band by a git
# credential helper instead, so every artefact above carries the LITERAL string
# `$TOKEN` and git expands it, in the helper's own shell, only at credential
# time.

BOT_TOKEN = "BOT-PAT-VALUE-not-a-real-credential"
REPO = "szymonrychu/tatara-helmfile"
PLAIN_URL = f"https://github.com/{REPO}.git"


@pytest.fixture
def checkout(tmp_path):
    """A checkout whose origin still points somewhere else, like the runner's."""
    d = tmp_path / "checkout"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(
        ["git", "-C", str(d), "remote", "add", "origin", "https://example.invalid/x"],
        check=True,
    )
    return d


def test_authenticate_origin_sets_the_plain_url_and_keeps_the_token_out_of_it(
    stub, checkout
):
    r = stub.run(
        f'cd "{checkout}"\nauthenticate_origin {REPO}\n', env={"TOKEN": BOT_TOKEN}
    )
    assert r.returncode == 0, r.stderr
    url = subprocess.run(
        ["git", "-C", str(checkout), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert url == PLAIN_URL
    assert BOT_TOKEN not in (checkout / ".git" / "config").read_text(encoding="utf-8")


OTHER_TOKEN = "WRONG-IDENTITY-from-a-global-helper"


@pytest.fixture
def rival_global_helper(tmp_path):
    """A global credential helper for github.com, already installed.

    NOT hypothetical. `credential.helper` is MULTI-VALUED: git runs system,
    then global, then local, and stops at the FIRST helper that answers with
    both a username and a password. `--local` is last in that list, not an
    override. The agent image ships exactly this shape (values/project-mtg/
    common.yaml calls it "the wrapper's global GIT_TOKEN credential helper"),
    and the ARC runner is shared.

    The userinfo URL this replaced was immune by accident - a URL carrying user
    AND password is already a complete credential, so git consults no helper at
    all - so the masking arrived with the helper, and the test that should have
    caught it pointed GIT_CONFIG_GLOBAL at /dev/null instead.
    """
    cfg = tmp_path / "gitconfig-global"
    cfg.write_text(
        "[credential]\n"
        '\thelper = "!f() { echo username=x-access-token; '
        'echo password=%s; }; f"\n' % OTHER_TOKEN,
        encoding="utf-8",
    )
    return cfg


def fill(checkout, global_cfg=os.devnull, token=BOT_TOKEN):
    return subprocess.run(
        ["git", "-C", str(checkout), "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "TOKEN": token,
            "GIT_CONFIG_GLOBAL": str(global_cfg),
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
    ).stdout


def test_the_helper_hands_git_the_token_from_the_environment(stub, checkout):
    """Out of band or not, it still has to actually authenticate."""
    r = stub.run(
        f'cd "{checkout}"\nauthenticate_origin {REPO}\n', env={"TOKEN": BOT_TOKEN}
    )
    assert r.returncode == 0, r.stderr
    filled = fill(checkout)
    assert "username=x-access-token" in filled
    assert f"password={BOT_TOKEN}" in filled


def test_a_global_helper_does_not_mask_the_bot_pat(stub, checkout, rival_global_helper):
    """The push must authenticate as the bot, or the pin lands as nobody.

    A masked helper is not a loud failure: the rival answers, the push
    authenticates as some other identity, and what comes back is a 403 that
    looks exactly like a token-scope problem.
    """
    r = stub.run(
        f'cd "{checkout}"\nauthenticate_origin {REPO}\n',
        env={"TOKEN": BOT_TOKEN, "GIT_CONFIG_GLOBAL": str(rival_global_helper)},
    )
    assert r.returncode == 0, r.stderr
    filled = fill(checkout, rival_global_helper)
    assert f"password={BOT_TOKEN}" in filled
    assert OTHER_TOKEN not in filled


def test_authenticate_origin_drops_the_checkout_credential_header(stub, checkout):
    """actions/checkout persists `http.<url>.extraheader: AUTHORIZATION: basic`.

    Git sends it on every request, so the server answers 200/403 rather than
    401 - and git only consults a credential helper on a 401. The header wins
    over the helper without either of them failing, and the push lands as the
    default GITHUB_TOKEN, read-only wherever default workflow permissions are
    restrictive.
    """
    subprocess.run(
        [
            "git", "-C", str(checkout), "config", "--local",
            "http.https://github.com/.extraheader",
            "AUTHORIZATION: basic Zm9vOmJhcg==",
        ],
        check=True,
    )
    r = stub.run(
        f'cd "{checkout}"\nauthenticate_origin {REPO}\n', env={"TOKEN": BOT_TOKEN}
    )
    assert r.returncode == 0, r.stderr
    got = subprocess.run(
        ["git", "-C", str(checkout), "config", "--local", "--get-regexp", "^http\\."],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "extraheader" not in got, got


@pytest.mark.parametrize("how", ["empty", "unset"])
def test_an_absent_token_fails_loudly_instead_of_authenticating_as_nobody(
    stub, checkout, how
):
    """`set -u` cannot catch this: bash never dereferences $TOKEN.

    The helper emits `password=` and git accepts that as a COMPLETE credential,
    so the push 403s with nothing in the log pointing at the missing input.
    Composite actions do not enforce `required: true` at runtime.
    """
    prelude = "unset TOKEN\n" if how == "unset" else ""
    r = stub.run(
        f'cd "{checkout}"\n{prelude}authenticate_origin {REPO}\n', env={"TOKEN": ""}
    )
    assert r.returncode != 0
    assert "TOKEN" in r.stderr


def test_the_helper_is_local_to_the_repo(stub, checkout):
    """The ARC runner is shared; a --global helper would outlive the job."""
    r = stub.run(
        f'cd "{checkout}"\nauthenticate_origin {REPO}\n', env={"TOKEN": BOT_TOKEN}
    )
    assert r.returncode == 0, r.stderr
    assert "credential" in (checkout / ".git" / "config").read_text(encoding="utf-8")


def test_clone_authed_clones_the_plain_url_and_carries_the_helper_into_the_clone(stub):
    """mode=bump clones a DIFFERENT repo, then fetches and pushes against it.

    So the helper has to be in the new repo's config, not just in the clone
    command: `git clone --config` applies it before the initial fetch AND
    leaves it behind for every later fetch/push.
    """
    stub.stub_git()
    r = stub.run(f"clone_authed {REPO} /nonexistent/work\n", env={"TOKEN": BOT_TOKEN})
    assert r.returncode == 0, r.stderr
    argv = stub.git_argv()
    assert PLAIN_URL in argv
    assert "--config credential.helper=" in argv
    assert BOT_TOKEN not in argv, "the credential must never reach argv"


def test_clone_authed_resets_the_helper_list_before_adding_its_own(stub):
    """Same multi-valued trap as authenticate_origin, one config layer down.

    `git clone --config` writes the CLONE's local config, which is still last
    behind the runner's global helper. An empty value first resets the list.
    """
    stub.stub_git()
    r = stub.run(f"clone_authed {REPO} /nonexistent/work\n", env={"TOKEN": BOT_TOKEN})
    assert r.returncode == 0, r.stderr
    argv = stub.git_argv()
    assert "--config credential.helper= --config credential.helper=!f()" in argv, argv


def test_no_git_url_anywhere_carries_a_credential():
    """The static guard: neither the lib nor the action may reintroduce it."""
    for path in (LIB, ACTION):
        text = path.read_text(encoding="utf-8")
        assert "authed_remote" not in text, f"{path.name} still builds a credential URL"
        for lineno, line in enumerate(text.splitlines(), 1):
            for word in line.split("#", 1)[0].split():
                if "://" not in word:
                    continue
                host = word.split("://", 1)[1].split("/", 1)[0]
                assert "@" not in host and "TOKEN" not in word, (
                    f"{path.name}:{lineno}: credential in a URL: {word}"
                )


# --- mode=tag: the semver label read ----------------------------------------
#
# Same failure shape as the bump step, opposite blast radius: a `|| true` over
# the whole `gh pr list | grep | cut` pipeline turns a 503 into an empty
# `level`, and the step then reports "no semver:(major|minor|patch) label;
# refusing to tag" on a release that MUST be tagged. The block is scanned out
# of the action and run for real, the way test_cd_release_terminal_noop.py runs
# the no-op discriminator.

LABEL_READ_START = "# 1. find the merged PR"
LABEL_READ_END = 'echo "significance level: $level"'


def label_read_block():
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if LABEL_READ_START in ln]
    assert len(starts) == 1, f"expected one label read, found {len(starts)}"
    ends = [i for i, ln in enumerate(lines) if LABEL_READ_END in ln]
    assert len(ends) == 1, f"expected one level echo, found {len(ends)}"
    return textwrap.dedent("\n".join(lines[starts[0] : ends[0]]))


def read_level(stub, plan):
    stub.plan("pr-list", plan)
    return stub.run(
        'PY="$(command -v python3)"\nSHA=deadbeef\n' + label_read_block() + "\necho LEVEL=[$level]\n"
    )


def test_the_label_is_read_off_the_merged_pr(stub):
    payload = '[{"number":423,"labels":[{"name":"area/cd"},{"name":"semver:minor"}]}]'
    r = read_level(stub, [f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[minor]" in r.stdout


def test_a_forge_failure_is_not_reported_as_a_missing_label(stub):
    """The wrong diagnosis: a 503 must not read as "refusing to tag"."""
    r = read_level(stub, ["1", "1", "1", "1", "1"])
    assert r.returncode != 0
    assert "LEVEL=" not in r.stdout
    err = r.stdout + r.stderr
    assert "could not read" in err
    assert "no semver" not in err, "a transient forge error is not a missing label"


def test_a_genuinely_unlabelled_pr_still_refuses_to_tag(stub):
    """gh exits 0 with an empty label list; that IS the refusal case."""
    r = read_level(stub, ['0 [{"number":423,"labels":[]}]'])
    assert r.returncode != 0
    assert "no semver" in r.stdout + r.stderr


def test_a_transient_failure_before_a_good_read_is_retried(stub):
    payload = '[{"number":423,"labels":[{"name":"semver:major"}]}]'
    r = read_level(stub, ["1", f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[major]" in r.stdout


def test_a_lookalike_label_is_not_mistaken_for_a_semver_level(stub):
    """Parsed, not grepped: `not-semver:major` in the JSON is not a level."""
    payload = (
        '[{"number":423,"labels":[{"name":"not-semver:major"},{"name":"semver:patch"}]}]'
    )
    r = read_level(stub, [f"0 {payload}"])
    assert r.returncode == 0
    assert "LEVEL=[patch]" in r.stdout


# --- action.yml wiring ------------------------------------------------------


def test_every_step_that_sets_errexit_also_inherits_it_into_substitutions():
    """`set -e` alone does not survive `x="$(f)"`. That is the whole defect.

    Pairwise, not by count: two `shopt`s in one step and none in another sums
    to the same total and leaves a step running with errexit off inside every
    command substitution it makes.
    """
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    setters = [i for i, ln in enumerate(lines) if ln.strip() == "set -euo pipefail"]
    assert setters, "no step sets errexit at all"
    for i in setters:
        assert lines[i + 1].strip() == "shopt -s inherit_errexit", (
            f"line {i + 1} sets errexit without inheriting it: {lines[i + 1].strip()!r}"
        )


def test_the_action_defines_no_forge_helpers_of_its_own():
    """One implementation, in the lib the tests cover. No drifting copy."""
    text = ACTION.read_text(encoding="utf-8")
    for helper in ("open_or_reuse_pr()", "arm_pr()", "ensure_label()"):
        assert helper not in text, f"{helper} must live in gh-lib.sh"
    assert 'source "$ACTION_PATH/gh-lib.sh"' in text


def test_the_action_never_swallows_a_forge_read_with_or_true():
    """`|| true` on a read is how a 503 became "no semver label; refusing to tag".

    `|| :` and `|| echo ...` swallow a status just as completely, so the guard
    covers the whole family rather than the one spelling that caused #622.
    """
    swallows = ("|| true", "|| :", "|| echo", "||true")
    for path in (ACTION, LIB):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.strip()
            if code.startswith("#"):
                continue
            reads_forge = "gh " in code or "git fetch" in code or "git clone" in code
            if reads_forge and any(s in code for s in swallows):
                raise AssertionError(f"{path.name}: forge read swallowed: {code}")
