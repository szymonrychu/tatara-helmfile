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
