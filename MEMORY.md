# MEMORY.md

Component-local memory for tatara-helmfile. Cross-repo decisions live in
`~/Documents/tatara/MEMORY.md`.

Format: `YYYY-MM-DD - decision/finding`

---

## Decisions

2026-05-27 - Repo created. Mirrors `~/Documents/infra/helmfile` structure (`.hook.sh`, per-release `values/<name>/{common,default,default.secrets}.yaml`). Owns helm releases for the tatara namespace except the bootstrap `tatara-argo-workflows` release, which stays in infra to avoid the chicken-and-egg (cluster needs CWTs to self-deploy).
2026-05-27 - First sync pinned to `tatara-memory 0.1.4` (Harbor no longer has 0.1.3; cluster runs 0.1.3 but helmfile diff shows only label/image-tag bump, no config changes). First apply will upgrade 0.1.3 -> 0.1.4 in-place.

## Dead-ends / things tried that did not work

*(nothing yet)*

## Open questions

*(nothing yet)*
