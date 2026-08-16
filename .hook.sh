#!/bin/bash
set -e
set -o nounset
set -o pipefail

readonly CURRENT_PWD="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

if [[ -z "${DEFAULT_TIMEOUT_S:-}" ]]; then
  readonly DEFAULT_TIMEOUT_S='600'
fi

fail() {
    local output="${1}"
    printf "%s\n" "${output}" >&2
    exit 1
}

# The loops below recover find's traversal status with `wait "$!"`, and $! is
# only set for a process substitution from bash 5.1. On 5.0 and older this
# script would die under `nounset` with `$!: unbound variable` - fail-closed,
# but pointing nowhere near the cause, on the cluster's only apply path. The
# runner image is not pinned in this repo, so say it out loud.
if ((BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1))); then
    fail "bash >= 5.1 required, found ${BASH_VERSION}: \$! is not set for a process substitution before 5.1"
fi

readonly HELMFILE_DIR="${1:-}"
readonly EVENT_NAME="${2:-}"
readonly RELEASE_NAME="${3:-}"
readonly RELEASE_NAMESPACE="${4:-}"
readonly RELEASE_VERSION="${5:-}"
readonly TIMEOUT_S="${6:-$DEFAULT_TIMEOUT_S}"

[[ -z "${HELMFILE_DIR}" ]] && fail "Missing 1st parameter HELMFILE_DIR"
[[ -z "${EVENT_NAME}" ]] && fail "Missing 2nd parameter EVENT_NAME"

# Wrapped in coreutils `timeout` because kubectl's own --wait/--timeout are
# prune/delete-scoped ("length of time to wait before giving up on a delete"),
# so --wait=true without --prune bounds nothing. Unbounded was survivable while
# a hung apply was silently green; now that a failure is fatal, a hang would
# instead park cd/deploy-train behind the tatara-helmfile-apply concurrency
# group, which serializes every apply. `timeout` exits 124, which is > 1 and
# therefore fatal under every event.
if [[ "${EVENT_NAME}" == "presync" ]]; then
    readonly KUBECTL=(timeout "${TIMEOUT_S}s" kubectl apply --wait=true)
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "prepare" ]]; then
    readonly KUBECTL=(timeout "${TIMEOUT_S}s" kubectl diff)
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "postsync" ]]; then
    readonly KUBECTL=(timeout "${TIMEOUT_S}s" kubectl apply --wait=true)
    readonly SEARCH_SUFF="post"
else
    fail "Not implemented event: '${EVENT_NAME}'"
fi
readonly TIMEOUT_TMSTP="$(($(date +%s) + TIMEOUT_S))"

# The global raw tree runs with no release, so RELEASE_NAMESPACE can be empty.
# `-n ""` is not the same as omitting it.
NS_ARGS=()
if [[ -n "${RELEASE_NAMESPACE}" ]]; then
    NS_ARGS=(-n "${RELEASE_NAMESPACE}")
fi
readonly NS_ARGS

# Accumulated failures, reported together at the end. A shell variable and
# never a temp path on purpose: helmfile invokes this hook once per release and
# may do so concurrently, so a shared marker file would cross-contaminate.
FAILURES=()

note_failure() {
    printf "::error::%s\n" "${1}" >&2
    FAILURES+=("${1}")
}

# `kubectl diff` exits 1 when there IS a difference, which is the expected
# result of the `prepare` event (the PR gate) and not a failure. Anything else,
# and anything at all under presync/postsync, is. Same 0 / expected-nonzero /
# error split diff.yaml applies to `helmfile diff --detailed-exitcode`.
is_expected_rc() {
    local rc="${1}"
    [[ "${rc}" -eq 0 ]] && return 0
    [[ "${EVENT_NAME}" == "prepare" && "${rc}" -eq 1 ]] && return 0
    return 1
}

# `find -exec cmd \;` reports find's OWN traversal status, never cmd's, so
# every one of these call sites used to report success on a rejected manifest
# (#398). Loop instead. Three mechanics each silently un-fix that fix:
#
#   - `find | while` runs the body in a SUBSHELL, where FAILURES is appended to
#     a copy that dies with the pipe. Read from a process substitution instead.
#   - a process substitution then discards find's OWN traversal status - the one
#     thing find's exit code did report, and which the pre-fix `set -e` caught.
#     `wait "$!"` after the loop recovers it (bash sets $! for a procsub).
#   - the loop body inherits the NUL file list as stdin, so anything that reads
#     stdin eats the remaining matches and the loop stops early. Every invoked
#     command gets `< /dev/null`; only the kubectl reading a sops pipe does not.
#
# mode is `plain` or `sops`.
apply_matching() {
    local dir="${1}" pattern="${2}" mode="${3}"
    local f rc
    local -a st

    while IFS= read -r -d '' f; do
        if [[ "${mode}" == "sops" ]]; then
            # Classify the two halves separately. Under pipefail a failed
            # `sops -d` whose kubectl still exits 0 surfaces as pipeline rc 1,
            # which under `prepare` is indistinguishable from "differences
            # found" - the decrypt failure would be swallowed all over again.
            printf "+ sops -d %s | %s %s -f -\n" "${f}" "${KUBECTL[*]}" "${NS_ARGS[*]}"
            set +e
            sops -d "${f}" < /dev/null | "${KUBECTL[@]}" "${NS_ARGS[@]}" -f -
            st=("${PIPESTATUS[@]}")
            set -e
            if [[ "${st[0]}" -ne 0 ]]; then
                note_failure "sops -d ${f}: exit ${st[0]}"
                continue
            fi
            rc="${st[1]}"
        else
            printf "+ %s %s -f %s\n" "${KUBECTL[*]}" "${NS_ARGS[*]}" "${f}"
            set +e
            "${KUBECTL[@]}" "${NS_ARGS[@]}" -f "${f}" < /dev/null
            rc="$?"
            set -e
        fi
        if is_expected_rc "${rc}"; then
            [[ "${rc}" -eq 0 ]] || printf "%s: differences found (kubectl diff exit %d)\n" "${f}" "${rc}"
        else
            note_failure "${KUBECTL[*]} -f ${f}: exit ${rc}"
        fi
    done < <(find "${dir}" -name "${pattern}" -print0)
    wait "$!" || note_failure "find ${dir} -name ${pattern}: exit $?"
}

# Hook scripts have no diff-exit convention, so any non-zero is a failure -
# under `prepare` too.
run_hooks() {
    local dir="${1}" pattern="${2}"
    local f rc

    while IFS= read -r -d '' f; do
        set +e
        bash -xe "${f}" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${RELEASE_VERSION}" < /dev/null
        rc="$?"
        set -e
        [[ "${rc}" -eq 0 ]] || note_failure "hook ${f}: exit ${rc}"
    done < <(find "${dir}" -name "${pattern}" -print0)
    wait "$!" || note_failure "find ${dir} -name ${pattern}: exit $?"
}

if [[ ! -z "${RELEASE_NAME}" ]] && [[ ! -z "${RELEASE_NAMESPACE}" ]]; then

    if [[ "${EVENT_NAME}" == "presync" ]] && kubectl get namespace "${RELEASE_NAMESPACE}" > /dev/null 2>&1; then
        printf "Checking '%s' helm relase health in '%s' namespace\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"

        faulty_release_revision=""
        while true; do
            release_json="$(helm list --all-namespaces --output json | jq -r ".[] | select(.name==\"${RELEASE_NAME}\" and .namespace==\"${RELEASE_NAMESPACE}\")")"
            if [[ -z "${release_json}" ]]; then
                printf "Release ${RELEASE_NAMESPACE}/${RELEASE_NAME} is missing, it's a fresh start!\n"
                break
            fi
            release_state="$(echo "${release_json}" | jq -r ".status")"
            if [[ "${release_state}" == "deployed" ]]; then
                printf "Release ${RELEASE_NAMESPACE}/${RELEASE_NAME} ok!\n"
                break
            elif [[ "${release_state}" == "failed" ]]; then
                printf "Previous release ${RELEASE_NAMESPACE}/${RELEASE_NAME} was 'failed', deleting faulty release from helm history!\n"
                release_revision="$(echo "${release_json}" | jq -r ".revision")"
                kubectl delete secret -n "${RELEASE_NAMESPACE}" "sh.helm.release.v1.${RELEASE_NAME}.v${release_revision}"
                break
            elif [[ "$(date +%s)" -ge "${TIMEOUT_TMSTP}" ]]; then
                printf "Timeout waiting for release ${RELEASE_NAMESPACE}/${RELEASE_NAME} to stabilize reached, deleting faulty release from helm history!\n"
                release_revision="$(echo "${release_json}" | jq -r ".revision")"
                kubectl delete secret -n "${RELEASE_NAMESPACE}" "sh.helm.release.v1.${RELEASE_NAME}.v${release_revision}"
                break
            fi
            sleep 1
        done
    fi

    readonly RELEASE_DIR="${CURRENT_PWD}/values/${RELEASE_NAME}"
    if [[ -d "${RELEASE_DIR}/raw" ]]; then
        printf "Searching for files to apply for '%s' in '%s' namespace!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        apply_matching "${RELEASE_DIR}/raw" "*.common.${SEARCH_SUFF}.yaml" plain
        apply_matching "${RELEASE_DIR}/raw" "*.common.${SEARCH_SUFF}.secrets.yaml" sops
        apply_matching "${RELEASE_DIR}/raw" "*.${RELEASE_NAME}.${SEARCH_SUFF}.yaml" plain
        apply_matching "${RELEASE_DIR}/raw" "*.${RELEASE_NAME}.${SEARCH_SUFF}.secrets.yaml" sops
    fi
    if [[ -d "${RELEASE_DIR}/hooks" ]]; then
        printf "Searching for hook scripts to run for '%s' in '%s'!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        run_hooks "${RELEASE_DIR}/hooks" "*.common.${SEARCH_SUFF}.sh"
        run_hooks "${RELEASE_DIR}/hooks" "*.${RELEASE_NAME}.${SEARCH_SUFF}.sh"
    fi
fi
printf "Running hook globally during '%s'\n" "${EVENT_NAME}"
if [[ -d "${CURRENT_PWD}/raw" ]]; then
    printf "Searching for files to apply!\n"
    apply_matching "${CURRENT_PWD}/raw" "*.${SEARCH_SUFF}.yaml" plain
    apply_matching "${CURRENT_PWD}/raw" "*.${SEARCH_SUFF}.secrets.yaml" sops
fi

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
    printf "\n%d failure(s) during '%s':\n" "${#FAILURES[@]}" "${EVENT_NAME}" >&2
    printf "  - %s\n" "${FAILURES[@]}" >&2
    exit 1
fi
