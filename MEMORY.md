# MEMORY - tatara-helmfile

- 2026-06-13 Repo created by full-extract of `infra/helmfile/helmfiles/tatara/`.
  Intentionally REVERSES the 2026-06-05 consolidation (`tatara-helmfile-into-infra`).
  Rationale: scope bot deploy access to one dedicated repo, not the whole 60+
  release homelab infra repo.
- 2026-06-13 Flat layout: hook at repo root (`./.hook.sh`), values at
  `values/<release>/raw/`. The infra hook's `PHASE`/`helmfiles/<phase>/` path
  derivation was dropped; `RELEASE_DIR=${PWD}/values/<release>`.
- 2026-06-13 `default.secrets.yaml` copied byte-for-byte from infra (same PGP
  recipient D39E...CED8). NEVER re-encrypted or printed. Same key means no
  rotation; key rotation is out of scope (noted as risk).
- 2026-06-13 Deploy runner SA `tatara-helmfile-deployer` is cluster-admin
  scoped (the operator chart installs CRDs/ClusterRoles/webhooks). Single
  highest-risk element. Mitigations: dedicated SA, private bot-only-write repo,
  control-plane-pinned + maxRunners-capped runner that only runs this repo's
  workflows. The runner + SA + binding live in infra/helmfile/helmfiles/coding.
- 2026-06-13 Repository CRs (self-enroll + 6 components) carried here in
  `raw/repositories-tatara.tatara-operator.pre.yaml`. Infra bucket had ONLY the
  Project CR, no Repository CRs (audit confirmed) - extracting orphaned nothing.
  Component CRs were previously applied ad-hoc from operator deploy-samples; now
  declarative from this repo.
- 2026-06-13 mise dropped infra-only tools (terraform/kustomize/kubectx/stern);
  kept helm/helmfile/kubectl/sops + helm-secrets 4.7.4 + helm-diff.
