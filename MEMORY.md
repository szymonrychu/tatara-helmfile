# MEMORY - tatara-helmfile

- 2026-06-21 (#47 MR pipeline failure) **diff workflow broke on large diffs: pass the sticky-comment body via `path:` (file), not an inline `message:` expression.** The `helmfile-diff` job went red NOT on the diff (it is `continue-on-error`) but on the `marocchino/sticky-pull-request-comment` step: `An error occurred trying to start process ... Argument list too long`. The whole diff was interpolated inline via `${{ steps.diff.outputs.output }}`; the operator forward-roll (c8678a6 -> 30d8c8e) pulled in the new alerting/dashboard manifests, making the diff large enough to overflow the child-process arg/env limit. Fix: `.github/workflows/diff.yaml` writes the body to `diff-comment.md` and uses the action's `path:` input, truncating to 60000 bytes (under GitHub's 65536-char comment cap) with a notice. LESSON: never interpolate unbounded command output into a GHA `with:`/expression - render to a file and use `path:`. SEPARATE pre-existing apply-time issue surfaced in the same diff: live `Project/infrastructure` (old raw manifest, pre-#45 migration) lacks Helm ownership metadata so the `project-infrastructure` release cannot adopt it; needs out-of-band `kubectl label/annotate` (app.kubernetes.io/managed-by=Helm + meta.helm.sh/release-name/-namespace) before apply. project-tatara adopts fine.

- 2026-06-21 (#45/#46 follow-up, default-branch apply failure) **Harbor retention GCs old `0.0.0-<sha>` chart tags: bumped tatara-operator pin c8678a6 -> 30d8c8e.** After #46 fixed the project-* pins, the main `apply` STILL failed (run 27899776035): `helm pull oci://.../tatara-operator:0.0.0-c8678a6` -> `FetchReference ... not found`. project-tatara + tatara-chat pulled fine in the same run, so it was not a login/registry problem - c8678a6 (operator main of 2026-06-20, now 7 commits behind HEAD) had its chart tag pruned from Harbor. The operator chart job (`.github/workflows/ci.yml`) packages charts/* as `0.0.0-${GITHUB_SHA:0:7}` on every push to main, so any current main sha is published; 30d8c8e is main HEAD and its tatara-project tag already pulled successfully here, proving the operator tag at the same sha is published too. Bumped tatara-operator to 0.0.0-30d8c8e (helmfile.yaml.gotmpl + README table), in lockstep with the project-* pins so the operator CRDs match the tatara-project chart the projects render. This also deploys the 6 intervening operator main commits (triage/brainstorm + #106 which added charts/tatara-project) - a benign forward roll, not a behavior change to the deploy. LESSON: never pin a `0.0.0-<sha>` that is more than a few main commits old; track main HEAD, because Harbor prunes old tags and a stale pin fails apply with chart-not-found.

- 2026-06-20 (operator#74, tatara/task-scan-qe-fbb9g) **Project/Repository CRs migrated from raw presync manifests to the tatara-project chart (subtask 5 of 5).** Deleted the 4 raw manifests (raw/project-{tatara,infrastructure} + repositories-{tatara,infrastructure}.tatara-operator.pre.yaml) and added two helm releases `project-tatara` + `project-infrastructure` (chart oci://harbor.szymonrichert.pl/charts/tatara-project, the new rook-ceph-cluster-style chart from operator#74 subtask 4), each consuming values/project-*/common.yaml. KEY POINTS: (1) Behavior-preserving - verified by rendering the local chart with the helmfile value-merge (values/common.yaml + values/default.yaml + values/<release>/common.yaml) and `yq`-normalizing kind+name+spec: BYTE-IDENTICAL to the deleted manifests (tatara 8 docs, infrastructure 4 docs). (2) Both releases `needs: [tatara/tatara-operator]` so the tatara.dev CRDs exist before the CRs apply (stronger than the old ordering, which relied on CRDs persisting across syncs since the raw manifests applied in the operator's OWN presync before its chart). (3) The infrastructure-scm sops Secret STAYS in values/tatara-operator/raw (applied by the operator presync); the chart only references scmSecretRef and never creates secret material (cluster-agnostic rule 14). tatara-scm is still the out-of-band cluster-managed Secret. (4) Chart version pinned 0.1.0 as a PLACEHOLDER - must be bumped to the published 0.0.0-<sha> once the tatara-project chart CI publishes (this MR depends on subtask 4 merging); until then the PR's sticky helmfile diff will show chart-not-found, which is the intended safety net. (5) Did NOT add speculative agent-customization fields (systemPrompt/env/plugins/...) to the LIVE projects - keeping specs byte-identical was the priority (behavior-preserving verification + rule 4 no-risk); the fields are available via the chart and demonstrated in the chart's deploy-samples/tatara-project-values.yaml. `helmfile list` confirms all 4 releases parse.

- 2026-06-14 **helm 4 server-side apply: dropped `helmDefaults.force: true`, added `--force-conflicts`.** helm 4 applies server-side (`--server-side=auto`); `force: true` (-> `--force-replace`) is INCOMPATIBLE ("cannot use server-side apply and force replace together") - the first real operator upgrade (2725b94) failed on it (earlier applies only worked because they were no-ops). SSA also means field-ownership matters: prior `kubectl set image` / `kubectl patch` left `kubectl-set` owning the Deployment `.image` and `kubectl-patch` owning `tatara-scm .data.token`, so a clean SSA apply needs `--force-conflicts` to reclaim them (the deploy bot is the sole GitOps owner). SECRET STALENESS: the verbatim-copied sops `default.secrets.yaml` was STALE vs the live (out-of-band-patched) secrets for ALL operator keys (scmToken, anthropicOauthToken, openaiApiKey, cliOidcClientId/Secret) - applying would have clobbered the working live values; resynced all 7 sops keys to live (hash-verified equal) so apply changes only the chart-version label, never secret data. Mapping is in the operator chart `templates/{managed-secrets,secret}.yaml`.
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
- 2026-06-20 GitLab `infrastructure` Project enrolled (containers/charts/helmfile,
  public gitlab.com/szymonrychu): `raw/project-infrastructure...pre.yaml` +
  `raw/repositories-infrastructure...pre.yaml` + sops `infrastructure-scm` Secret
  (own scmSecretRef, NOT tatara-scm; keys token=api-PAT + webhookSecret). First
  GitLab provider Project; full agent loop (brainstorm+issue/MR scans), own memory
  stack (pgInstances 1). botLogin szymonrychu-bot, maintainerLogins [szymonrychu].
  Webhook receiver /operator/webhooks/infrastructure (X-Gitlab-Token=webhookSecret).
  Runbook: tatara/docs/superpowers/runbooks/2026-06-20-enroll-infrastructure-gitlab.md.
- 2026-06-20 **CRD-adopt presync hook** (`values/tatara-operator/hooks/crd-adopt.tatara-operator.pre.sh`).
  operator #89 moved the 5 tatara.dev CRDs from install-only `crds/` to templated `crd-bases/`, so
  `helm upgrade` now ADOPTS them; a CRD pre-existing WITHOUT helm ownership metadata fails apply with
  "invalid ownership metadata" (blocks the whole release). The live cluster was relabelled out-of-band
  once to land #38/#39; this idempotent presync hook (`kubectl label/annotate` iff `managed-by` absent)
  makes the fix permanent + self-healing. No-op on the normal path (CRDs already owned) and on fresh
  clusters (helm installs the CRDs, none pre-exist). NOTE the operator chart RBAC is hand-maintained
  (`templates/rbac.yaml`) and the kubebuilder:rbac markers are NOT consumed (operator `manifests` runs
  controller-gen crd only) - queuedevents RBAC was added by hand in operator #90; root fix (wire
  controller-gen rbac) is a tatara-operator backlog item.
- 2026-06-20 apply.yaml "reconcile enrollment CRs" step did `kubectl apply -f values/tatara-operator/raw/`
  (whole dir) assuming raw/ holds only non-secret manifests. The infrastructure-scm sops Secret (first
  raw/ `.secrets.yaml`) broke it: kubectl validated ciphertext -> "unexpected GroupVersion string: ENC[...]".
  Fix: step now mirrors .hook.sh - plain `*.yaml` applied directly, `*.secrets.yaml` sops-decrypted first.
  Incident: applied the Secret live (`sops -d | kubectl apply`) since the no-op apply skipped the hook.
- 2026-06-20 Grafana incident-response ENABLED for the infrastructure Project (first project to use the
  inert feature). One PR: spec.grafana{enabled,url=http://prometheus-grafana.monitoring.svc.cluster.local,
  secretRef=infrastructure-grafana} + agent.image bumped 7d65446->cc1d7db (grafana-capable wrapper) +
  grafanaMcpImage=grafana/mcp-grafana:0.11.4 (operator-wide chart key, was empty; only projects with
  spec.grafana.enabled provision an mcp). tatara has NO grafana secret/token - the SA token reused was the
  ONLY one in the cluster (ai/grafana-mcp key `token`, glsa_), user-supplied. grafana secret keys:
  serviceAccountToken (mcp) + webhookSecret (alert bearer, server.go:1004). Depends on #29 (raw-secret
  apply fix) merging first or the no-op fallback chokes on the 2 encrypted files.

2026-06-21 (#45 follow-up) **Default-branch apply failed: tatara-project pinned at the unpublished placeholder `0.1.0`.** #45 merged with the helmfile.yaml.gotmpl placeholder `version: 0.1.0` on both project-tatara + project-infrastructure still in place (the bump-before-merge step in the 2026-06-20 entry never happened). operator CI ONLY publishes `0.0.0-<short-sha>` (it `helm package --version 0.0.0-${SHORT_SHA}` over `charts/*` on push to main); the chart's own `Chart.yaml` `version: 0.1.0` is never pushed to Harbor. So `helmfile -e default apply` on main died with chart-not-found pulling `oci://.../tatara-project:0.1.0`. Fix: pinned both project releases to `0.0.0-30d8c8e` (operator main HEAD, which carries `charts/tatara-project`). Verified by `helm template`-ing the local operator checkout's chart at 30d8c8e with the exact helmfile value-merge: project-tatara -> 1 Project + 7 Repository, project-infrastructure -> 1 Project + 5 Repository, all valid. Did NOT bump tatara-operator (stays `0.0.0-c8678a6`, already deployed/working); the tatara-project chart only emits CRs the running operator reconciles, no operator restart needed. LESSON: never merge a helmfile pinning an unpublished version; the PR's sticky diff showing chart-not-found is the merge-blocker, not a soft warning.

2026-06-21 (tatara-operator#102) Set scm.maintainerLogins AND scm.reporterLogins to [szymonrychu] on BOTH project-tatara and project-infrastructure. The operator's new reporter-intake gate only takes effect when reporterLogins is non-empty (empty = open, and third-party #56 autoapprove stays open), so populating reporterLogins here is what actually closes the prompt-injection vector. maintainerLogins makes only szymonrychu's comment count as approval. The bot (szymonrychu-bot) is always a trusted insider, so autonomous brainstorm/health-check issues still flow. The tatara-project chart renders project.spec verbatim via toYaml, so no chart bump was needed for the new fields.
