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
