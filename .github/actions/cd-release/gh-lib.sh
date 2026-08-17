# shellcheck shell=bash
# Forge helpers for the cd-release composite action. Sourced via $ACTION_PATH,
# the same way apply-pins.py is invoked. Covered by test_gh_lib.py.
#
# Every function returns an EXPLICIT status and never leans on `set -e` to
# propagate a failure, because `set -e` does not survive the one place that
# matters: bash unsets errexit inside a command substitution unless
# `inherit_errexit` is on, so `pr_url="$(open_or_reuse_pr ...)"` runs its body
# with errexit OFF. On 2026-08-17 (run 32035597231 attempt 4) that turned a
# transient 503 from `gh pr create` into an EMPTY url and a zero exit: the pin
# was already pushed to cd/deploy-train, no PR was opened, nothing was armed,
# and the deploy cascade stopped dead until a human opened the PR by hand.
#
# The rule that falls out of it, and that the tests pin: a forge read that
# FAILED must never be reported as "the thing is not there".

GH_RETRY_ATTEMPTS="${GH_RETRY_ATTEMPTS:-5}"
GH_RETRY_SLEEP="${GH_RETRY_SLEEP:-5}"

# retry <label> <cmd...> - run cmd until it succeeds, up to GH_RETRY_ATTEMPTS,
# with linear backoff. Prints ONLY the successful attempt's stdout, so it is
# safe inside a command substitution; diagnostics go to stderr. Returns the
# last attempt's status.
#
# <label> is what gets logged - never argv, which carries the token in the
# authenticated git URLs this also wraps.
retry() {
  local label="$1"; shift
  local attempt=1 rc=0 out=""
  while :; do
    # rc is read in the else branch on purpose: an `if` whose condition fails
    # and which has no else returns 0, so `rc=$?` after `fi` reads 0 and every
    # exhausted retry would report success.
    if out="$("$@")"; then
      [ -n "$out" ] && printf '%s\n' "$out"
      return 0
    else
      rc=$?
    fi
    if [ "$attempt" -ge "$GH_RETRY_ATTEMPTS" ]; then
      echo "::warning::${label}: failed after ${attempt} attempts (status ${rc})" >&2
      return "$rc"
    fi
    echo "${label}: status ${rc}, retrying (${attempt}/${GH_RETRY_ATTEMPTS})" >&2
    sleep "$(( GH_RETRY_SLEEP * attempt ))"
    attempt=$(( attempt + 1 ))
  done
}

# pr_number <url> - print the trailing PR number. Fails on anything that is not
# a pull-request URL, which is what `pr_num="${pr_url##*/}"` on an empty url did
# not do: it POSTed `issues//labels` and 404'd.
pr_number() {
  local url="${1-}"
  if [[ ! "$url" =~ ^https://[^[:space:]]+/pull/([0-9]+)$ ]]; then
    echo "::error::not a pull-request URL: '${url}'" >&2
    return 1
  fi
  printf '%s\n' "${BASH_REMATCH[1]}"
}

# open_or_reuse_pr <repo> <branch> <title> <body> - print the PR url.
#
# Distinguishes "the list succeeded and found no PR" (open one) from "the list
# FAILED" (say so and stop). Prints nothing at all on any failure path, so a
# caller can never mistake an empty url for a real one.
open_or_reuse_pr() {
  local repo="$1" branch="$2" title="$3" body="$4" url=""

  if ! url="$(retry "gh pr list ${repo}#${branch}" \
      gh pr list --repo "$repo" --head "$branch" --state open --json url --jq '.[0].url')"; then
    echo "::error::could not list open PRs for ${repo}#${branch}; refusing to assume none exists." >&2
    return 1
  fi

  if [ -z "$url" ]; then
    if ! url="$(retry "gh pr create ${repo}#${branch}" \
        gh pr create --repo "$repo" --base main --head "$branch" --title "$title" --body "$body")"; then
      echo "::error::could not open a PR for ${repo}#${branch}; the pushed pin would be stranded behind no PR." >&2
      return 1
    fi
    # gh prints the url last; anything before it is chatter.
    url="$(printf '%s\n' "$url" | tail -n 1)"
  fi

  pr_number "$url" >/dev/null || return 1
  printf '%s\n' "$url"
}

# arm_pr <repo> <pr_url> <label> - label the PR and arm auto-merge, then READ
# THE STATE BACK. `gh pr merge --auto` exiting 0 is not evidence that anything
# is armed, and an unarmed PR never merges: the writer has to assert the same
# observable that verify-pin reads, or it reports success on a dead cascade.
arm_pr() {
  local repo="$1" url="${2-}" label="$3" num armed
  num="$(pr_number "$url")" || return 1

  # EnsureLabel-equivalent safety net; the operator owns the canonical colors
  # and the label almost always already exists, so failure here is expected.
  gh label create "$label" --repo "$repo" --color 0e8a16 \
    --description "tatara CD propagation bump" >/dev/null 2>&1 || true

  # REST, not `gh pr edit --add-label`: that runs a GraphQL query whose `login`
  # field demands read:org on classic PATs, and the bot PAT is repo-only.
  if ! retry "label ${repo}#${num}" \
      gh api -X POST "repos/${repo}/issues/${num}/labels" -f "labels[]=${label}" >/dev/null; then
    echo "::error::could not label ${url}; refusing to arm an unlabelled PR (CI cuts the tag from the label)." >&2
    return 1
  fi

  if ! retry "arm auto-merge ${repo}#${num}" \
      gh pr merge "$url" --repo "$repo" --auto --squash >/dev/null; then
    echo "::error::could not arm auto-merge on ${url}." >&2
    return 1
  fi

  if ! armed="$(retry "read auto-merge state ${repo}#${num}" \
      gh pr view "$url" --repo "$repo" --json autoMergeRequest --jq '.autoMergeRequest.enabledAt // empty')"; then
    echo "::error::could not read the auto-merge state back from ${url}." >&2
    return 1
  fi
  if [ -z "$armed" ]; then
    echo "::error::${url} is open but auto-merge is NOT armed; it would sit there forever." >&2
    return 1
  fi

  echo "armed auto-merge on ${url} (enabledAt ${armed})"
}
