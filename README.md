# tatara-helmfile

Standalone helmfile bucket for the tatara platform. Owns two helm releases
and the operator enrollment CRs, deploys via GitHub Actions on an in-cluster
ARC runner.

## Layout

```
helmfile.yaml.gotmpl          # single 'default' env, helmDefaults, 2 releases
.hook.sh                      # presync: applies values/<release>/raw/*.pre.yaml,
                              #   sops-decrypts *.pre.secrets.yaml
values/
  common.yaml                 # imagePullSecrets: regcred (bucket-wide)
  default.yaml                # env-level (empty)
  tatara-operator/
    common.yaml               # image.tag pin
    default.yaml              # ingress/webhook/OIDC/memory-image values
    default.secrets.yaml      # sops (PGP D39E...CED8)
    raw/
      project-tatara.tatara-operator.pre.yaml        # tatara.dev Project CR
      repositories-tatara.tatara-operator.pre.yaml   # Repository CRs (incl. self-enroll)
  tatara-chat/
    default.yaml              # ingress host/path
.github/workflows/
  diff.yaml                   # PR -> helmfile diff -> sticky comment (non-blocking)
  apply.yaml                  # push main -> helmfile apply (concurrency-guarded)
```

## Releases

| release          | chart                                                | version       | ns     |
|------------------|------------------------------------------------------|---------------|--------|
| tatara-chat      | oci://harbor.szymonrichert.pl/charts/tatara-chat     | 0.1.0         | tatara |
| tatara-operator  | oci://harbor.szymonrichert.pl/charts/tatara-operator | 0.0.0-7d45bd9 | tatara |

## Deploy flow

1. Open a PR bumping a release (image tag in `values/tatara-operator/common.yaml`
   and/or chart `version:` in `helmfile.yaml.gotmpl`).
2. `diff.yaml` posts the rendered `helmfile diff` as a sticky PR comment.
3. Merge to `main`. `apply.yaml` runs `helmfile -e default apply` on the
   in-cluster runner. Failures roll back via helmDefaults `--rollback-on-failure`.

## Local use

```bash
mise install                                  # helm/helmfile/kubectl/sops + plugins
helm registry login harbor.szymonrichert.pl   # OCI chart pull
helmfile -e default diff                       # against current kube-context
```

## Auth

- Cluster: in-cluster ServiceAccount `tatara-helmfile-deployer` (no KUBECONFIG).
- Harbor: `HARBOR_USERNAME` / `HARBOR_PASSWORD` GH Actions secrets.
- sops: `GPG_PRIVATE_RSA_B64` GH Actions secret (base64 PGP private key).

The ARC runner scale set + SA + cluster-admin binding live in
`infra/helmfile/helmfiles/coding`, not here.
