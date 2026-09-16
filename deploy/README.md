# medi-bucher on OpenShift — operator runbook

Deploys the [medi-bucher](../README.md) daemon (Mediterana / MyWellness course booking) as a
single long-lived pod, with durable booking state. The manifests here are consumed by Argo CD
(ApplicationSet `external-manifests` in `ocp-gitops`) and deployed into namespace
`app-medi-bucher` — the namespace is created by Argo CD (`CreateNamespace=true`), which is why
there is no `namespace.yaml` here.

**Everything sensitive lives in Infisical**: the full `config.yaml` — accounts, targets, and
credentials — is a single secret. Nothing sensitive is in this repo.

## What you get

| File | Purpose |
|---|---|
| `externalsecret.yaml` | `medi-bucher-config` — syncs Infisical `MEDI_CONFIG` → Secret key `config.yaml` |
| `pvc.yaml` | `medi-bucher-history` — persists `booked_history.json` (this is *stateful*) |
| `deployment.yaml` | Deployment `medi-bucher` — mounts the Secret as config, PVC for state, probes, resources |

## Deploy

The ApplicationSet in the [`ocp-gitops`](https://github.com/CrowdSalat/ocp-gitops)
repository points at this repo's `deploy/` directory (`gitops/bootstrap/apps-applicationset-external-manifests.yaml`).
Merging to `main` here is picked up by Argo CD automatically (within the polling interval):

```bash
# Trigger immediate evaluation of the ApplicationSets (skip ~3 min polling wait):
oc annotate applicationset external-manifests -n openshift-gitops \
  argocd.argoproj.io/refresh=hard --overwrite

# Watch the generated Application:
oc -n openshift-gitops get application medi-bucher

# Once synced, wait for the pod and check the logs:
oc -n app-medi-bucher rollout status deploy/medi-bucher
oc -n app-medi-bucher logs deploy/medi-bucher
```

## The config lives in Infisical (never in git)

The daemon's config — accounts, targets, and credentials — is stored as a **single Infisical
secret** and synced to the `medi-bucher-config` Secret by `externalsecret.yaml`
(ExternalSecrets Operator → ClusterSecretStore `infisical` → `eu.infisical.com`, project `ocp`,
environment `prod`). It is mounted read-only at `/etc/booker/config.yaml` and read by the app
via `username`/`password` inline per account.

**To create/update it (e.g. first time, or changing targets):**
1. In Infisical (eu.infisical.com), add/update the secret **`MEDI_CONFIG`** with the full
   `config.yaml` text — see [config.example.yaml](../config.example.yaml) for the schema, with
   `username` and `password` filled in for each account.
2. The ExternalSecret refreshes every hour; the daemon re-mount does **not** auto-reload config,
   so after the refresh force a rollout to pick it up:
   ```bash
   oc -n app-medi-bucher rollout restart deploy/medi-bucher
   ```
3. Check the log shows the account as `available` and the targets listed:
   ```bash
   oc -n app-medi-bucher logs deploy/medi-bucher
   ```

Missing/invalid credentials are **not** fatal: the account is suspended and logged, the
other accounts keep running.

## Verify the config (safe, no booking happens)

Dry-run performs discovery only — no Book call is ever sent:

```bash
oc -n app-medi-bucher rsh deploy/medi-bucher -- booker --dry-run /etc/booker/config.yaml
```

Expect a line per planned burst like:

```
ACCOUNT=johanna TARGET=Body Pump CLASSID=… OPENS=2026-… STATUS=eligible
```

Real daemon log example: `ACCOUNT=johanna TARGET=Body Balance STATUS=booked`.

## One-shot runs (alternate mode, not shipped)

The Deployment runs the long-lived daemon (`booker /etc/booker/config.yaml`). If you prefer
per-window runs, create a CronJob (a small extra manifest) that uses the same
Secret/PVC wiring:

```yaml
schedule: "0 21 * * 2,4"           # adjust to your classes' release days
containers:
  - image: ghcr.io/crowdsalat/medi-bucher:latest
    args: ["--once", "/etc/booker/config.yaml"]
    # same config volumeMount (medi-bucher-config Secret) and /app PVC mounts as deployment.yaml
```

`--once` runs one discovery + burst pass for bursts due *now* and exits — safe for
scheduled execution. Remember: once recorded in `booked_history.json` a class is never
re-booked, so overlap with the daemon is harmless.

## State / backups

`booked_history.json` is written to `/app` (container WORKDIR), backed by the `medi-bucher-history`
PVC. **It prevents re-booking a class that already has a booked record** — losing it can make
the daemon re-book. Back it up:

```bash
# snapshot the file from a running pod (deploy/medi-bucher resolves to its first pod on OpenShift 4.x;
# use the pod name from `oc -n app-medi-bucher get pods` if it doesn't)
oc -n app-medi-bucher cp deploy/medi-bucher:/app/booked_history.json ./booked_history.backup.json

# if you need to clear one entry, edit the file and copy it back
```

The PVC type depends on your cluster default storage class (`pvc.yaml` omits
`storageClassName`). If your cluster has no default, uncomment and set one there.

## Image pull

`ghcr.io/crowdsalat/medi-bucher:latest` is a **public** GHCR package → anonymous pull works,
no `imagePullSecrets` on the Deployment.

> If the package ever becomes **private**: create a Secret with credentials, add
> `imagePullSecrets:` to the pod spec, and use the same Secret's values in the
> `--with-creds`-style flow GHCR documents. Concretely:
>
> ```bash
> oc -n app-medi-bucher create secret docker-registry ghcr-creds \
>   --docker-server=ghcr.io \
>   --docker-username=<your-gh-username> \
>   --docker-password=<PAT-or-token>
> ```
> then add to deployment.yaml's pod spec:
> ```yaml
> imagePullSecrets:
>   - name: ghcr-creds
> ```
> This needs no help from cluster admins (no global pull secret required).

## Updating

```bash
oc -n app-medi-bucher set image deploy/medi-bucher medi-bucher=ghcr.io/crowdsalat/medi-bucher:<new-tag>
oc -n app-medi-bucher rollout status deploy/medi-bucher
```

## Security posture (restricted-v2 SCC)

- No explicit `runAsUser`/`runAsGroup`/`fsGroup` — the SCC assigns the UID and the fsGroup
  from the project's range and chowns the PVC accordingly (the image runs as non-root).
- Pod spec only sets `runAsNonRoot: true`; the rest (`allowPrivilegeEscalation: false`,
  required capability drops, `seccompProfile: RuntimeDefault`) is provided by the SCC.
- No privileged containers, no host mounts, no hostNetwork.
- Credentials and targets never appear in git — only the (public) Infisical secret `MEDI_CONFIG`
  and the synced K8s Secret hold them.