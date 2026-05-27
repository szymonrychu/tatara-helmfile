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

readonly HELMFILE_DIR="${1:-}"
readonly EVENT_NAME="${2:-}"
readonly RELEASE_NAME="${3:-}"
readonly RELEASE_NAMESPACE="${4:-}"
readonly RELEASE_VERSION="${5:-}"
readonly TIMEOUT_S="${6:-$DEFAULT_TIMEOUT_S}"

[[ -z "${HELMFILE_DIR}" ]] && fail "Missing 1st parameter HELMFILE_DIR"
[[ -z "${EVENT_NAME}" ]] && fail "Missing 2nd parameter EVENT_NAME"

if [[ "${EVENT_NAME}" == "presync" ]]; then
    readonly KUBECTL="kubectl apply --wait=true"
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "prepare" ]]; then
    readonly KUBECTL="kubectl diff"
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "postsync" ]]; then
    readonly KUBECTL="kubectl apply --wait=true"
    readonly SEARCH_SUFF="post"
else
    fail "Not implemented event: '${EVENT_NAME}'"
fi
readonly PHASE="$(basename "$HELMFILE_DIR")"
readonly TIMEOUT_TMSTP="$(($(date +%s) + TIMEOUT_S))"

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

    readonly RELEASE_DIR="${CURRENT_PWD}/helmfiles/${PHASE}/values/${RELEASE_NAME}"
    if [[ -d "${RELEASE_DIR}/raw" ]]; then
        printf "Searching for files to apply for '%s' in '%s' namespace!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        find "${RELEASE_DIR}/raw" -name "*.common.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
        find "${RELEASE_DIR}/raw" -name "*.common.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
        find "${RELEASE_DIR}/raw" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
        find "${RELEASE_DIR}/raw" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
    fi
    if [[ -d "${RELEASE_DIR}/hooks" ]]; then
        printf "Searching for hook scripts to run for '%s' in '%s'!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        find "${RELEASE_DIR}/hooks" -name "*.common.${SEARCH_SUFF}.sh" -exec bash -xe "{}" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${RELEASE_VERSION}" \;
        find "${RELEASE_DIR}/hooks" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.sh" -exec bash -xe "{}" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${RELEASE_VERSION}" \;
    fi
fi
printf "Running hook globally during '%s'\n" "${EVENT_NAME}"
if [[ -d "${CURRENT_PWD}/raw" ]]; then
    printf "Searching for files to apply!\n"
    find "${CURRENT_PWD}/raw" -name "*.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
    find "${CURRENT_PWD}/raw" -name "*.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
fi
