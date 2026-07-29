# ROADMAP - tatara-helmfile

## Cross-repo follow-ups (not in this repo)

- [ ] infra: add `arc-runner-tatara-helmfile` RunnerScaleSet + ServiceAccount
      `tatara-helmfile-deployer` + cluster-admin ClusterRoleBinding in
      `infra/helmfile/helmfiles/coding`. Workflows here are RED until this ships.
- [ ] infra: remove the `helmfiles/tatara/` bucket from `infra/helmfile`
      (this repo is now sole owner). Drop the bucket from the root
      `helmfile.yaml.gotmpl` helmfiles index.
- [ ] wrapper: ship the `tatara-deploy-harness` skill + add `tatara-helmfile`
      to the agent's TATARA_REPOS (spec Sub-system D).

### Node-fault resilience (#245 / #239) - routed out of this repo

A node can be comprehensively broken (pod overlay dead, or CSI mounts failing)
while still reporting `Ready`, so nothing detects it, nothing drains it, and
pods bound to it sit there until the node happens to flap. This repo shipped
the only in-charter piece (the tatara-operator PodDisruptionBudget). The rest:

- [ ] tatara-observability: per-node **pod-unreachable ratio** rule - fraction
      of pod-network scrape targets on one node that are `up == 0`, joined
      through `kube_pod_info` and guarded by `kube_node_status_condition
      {condition="Ready",status="true"} == 1`, with a minimum-target guard.
      Fires on the #239 class directly instead of as N scattered victim alerts.
- [ ] tatara-observability: per-node **volume-plane wedge** rule - kubelet
      `volume_manager_total_volumes{state="desired_state_of_world"}` minus
      `{state="actual_state_of_world"}` sustained > 0 on a `Ready` node
      (kubelet is host-network, so it stays scrapeable through a pod-overlay
      partition). Fires on the #245 class. Optional KSM companion: pods in
      `CreateContainerError`/`CreateContainerConfigError` grouped by node.
- [ ] tatara-observability: add a `runbook_url` annotation to the
      `Operator replica missing` rule (`alerts/tatara-operator.yaml`, Grafana
      uid `efq61vw5dwe0we`) - it has none, and it was the only page for a 9h
      node partition.
- [ ] tatara-operator: expose the cnpg **storage class** (e.g.
      `Project.spec.memory.pgStorageClass` / `MEMORY_PG_STORAGE_CLASS`).
      `PGCluster()` sets no storageClass, so every Postgres data+WAL PVC
      inherits the cluster default `rook-ceph-rwx` (CephFS RWX) - the substrate
      whose stale mount produced #245's `stat ... permission denied`. The RBD
      block class `rook-ceph` exists and is the right substrate for RWO
      Postgres volumes. Migration is backup/restore (`PGClusterFromBackup`),
      not an in-place edit; the archive bucket already exists here
      (`raw/pg-backup-bucket.tatara-operator.pre.yaml`).
- [ ] infra (`infra/helmfile/helmfiles/coding`): node-problem-detector plus a
      remediation path (cordon/drain on a custom node condition). Deliberately
      NOT here - this repo is not the cluster bootstrap, and its deploy runner
      is cluster-admin scoped. Needs a maintainer appetite call first (#239 was
      parked on exactly that question).
### Observability-plane survivability (#256, un-ownable here)

The 2026-07-24 18:10-18:41Z reboot took Grafana 0/2 and `grafana-database-cluster`
0/3 at once, blanking every incident agent. Everything below is in namespace
`monitoring` or on the nodes themselves, owned by `infra/helmfile/helmfiles/coding`
and the node provisioning - NOT by this repo. See MEMORY.md 2026-07-28.

- [ ] infra: make node reboots graceful - cordon + drain + wait-for-Ready between
      nodes. This is the ONLY control that addresses the actual failure: the pods
      were removed by node-controller taint eviction, which bypasses every PDB.
      The measured 3m26s gap between two reboots is shorter than Grafana's
      recovery time.
- [ ] infra: label the nodes with a fault domain that distinguishes the
      simultaneously-rebooting group (`kubernetes-47d28x2`/`jhv07x2`/`5vv07x2`)
      from `worker-jtw3f33` + `nas-d0w363i`, then spread on that key. Plain
      `kubernetes.io/hostname` spread is already satisfied by the default
      scheduler plugin and buys nothing here.
- [ ] infra: raise the `prometheus-grafana` Deployment above 2 replicas (with
      `maxSkew: 1` / `DoNotSchedule` on hostname it would then be forced onto a
      4th node, outside the reboot group) and add a PDB for it - it is the one
      workload in `monitoring` with no PodDisruptionBudget at all.
- [ ] tatara-observability: no rule watches the observability plane itself, and
      a Grafana-managed rule cannot alert on Grafana being down. Needs an
      external probe / dead-man's-switch, plus a `runbookURL` on rule
      `afq61w81lyps1f`.

## This repo

- [ ] First live `helmfile apply` from main (human-gated, after runner exists).
- [ ] Confirm `kubectl get project tatara` + `kubectl get repository -n tatara`
      shows the self-enroll + 6 component CRs after first apply.
- [ ] Consider sops PGP key rotation (currently shared with infra).

## infrastructure (GitLab) Project enrollment

- [ ] Merge `feat/enroll-infrastructure-gitlab` PR; pipeline applies Project +
      3 Repositories + infrastructure-scm Secret.
- [ ] USER: add szymonrychu-bot Maintainer on the 3 GitLab repos + register
      webhooks (runbook Steps 2-3).
- [ ] Verify Repositories Ingesting->Ingested, memory stack up, status.webhookURL.
- [ ] ROTATE the szymonrychu-bot api PAT (pasted in chat transcript); re-encrypt.

## infrastructure Grafana incident-response (2026-06-20, `PR open`)

- [ ] Merge #29 (raw-secret apply fix) then this PR; operator restarts with
      GRAFANA_MCP_IMAGE, provisions mem? no - grafana-mcp-infrastructure pod.
- [ ] USER: create a Grafana alert contact point/webhook ->
      https://tatara.szymonrichert.pl/operator/webhooks/infrastructure/grafana
      Authorization: Bearer <webhookSecret> (read via sops from the grafana secret).
- [ ] Verify grafana-mcp-infrastructure pod Running; fire a test alert.
- [ ] ROTATE the glsa_ SA token (pasted in chat).
