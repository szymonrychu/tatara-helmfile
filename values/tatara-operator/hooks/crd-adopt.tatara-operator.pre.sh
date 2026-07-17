#!/bin/bash
# Presync guard: make the tatara.dev CRDs adoptable by the tatara-operator helm
# release. #89 (operator) moved the CRDs from install-only `crds/` to templated
# `crd-bases/`, so `helm upgrade` now ADOPTS them; a CRD that already exists on
# the cluster without helm ownership metadata fails the upgrade with "invalid
# ownership metadata" and blocks the whole release. The 2026-06-20 live cluster
# was relabelled out-of-band once to unblock; this hook makes the fix permanent
# and self-healing for any future cluster/CRD state.
#
# Idempotent: only stamps a CRD that exists and is NOT already owned by this
# release. On a fresh cluster the CRDs do not pre-exist (helm installs them
# cleanly), so this is a no-op there.
set -e
set -o nounset
set -o pipefail

readonly RELEASE_NAME="${1:?missing release name}"
readonly RELEASE_NAMESPACE="${2:?missing release namespace}"

readonly CRDS=(
  projects.tatara.dev
  repositories.tatara.dev
  tasks.tatara.dev
  subtasks.tatara.dev
  queuedevents.tatara.dev
)

for crd in "${CRDS[@]}"; do
  if ! kubectl get crd "${crd}" >/dev/null 2>&1; then
    continue
  fi
  managed_by="$(kubectl get crd "${crd}" -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}' 2>/dev/null || true)"
  release="$(kubectl get crd "${crd}" -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}' 2>/dev/null || true)"
  if [[ "${managed_by}" == "Helm" && "${release}" == "${RELEASE_NAME}" ]]; then
    continue
  fi
  printf "Adopting CRD %s into helm release %s/%s\n" "${crd}" "${RELEASE_NAMESPACE}" "${RELEASE_NAME}"
  kubectl label crd "${crd}" "app.kubernetes.io/managed-by=Helm" --overwrite
  kubectl annotate crd "${crd}" \
    "meta.helm.sh/release-name=${RELEASE_NAME}" \
    "meta.helm.sh/release-namespace=${RELEASE_NAMESPACE}" --overwrite
done
