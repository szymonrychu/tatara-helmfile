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
