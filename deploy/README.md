# booker on OpenShift — operator runbook

Deploys the [booker](../README.md) daemon (Mediterana / MyWellness course booking) as a
single long-lived pod, with durable booking state. The manifests here are consumed by Argo CD
(ApplicationSet `external-manifests` in `ocp-gitops`) and deployed into namespace
`app-medi-bucher` — the namespace is created by Argo CD (`CreateNamespace=true`), which is why
there is no `namespace.yaml` here.

## What you get

| File | Purpose |
|---|---|
| `configmap.yaml` | `booker-config` — daemon config (accounts, targets), mounted read-only |
| `externalsecret.yaml` | `booker-creds` — synced from Infisical by ExternalSecrets Operator |
| `pvc.yaml` | `booker-history` — persists `booked_history.json` (this is *stateful*) |
| `deployment.yaml` | Deployment `booker` — image, env, config mount, PVC mount, probes, resources |

## Deploy

The ApplicationSet in the [`ocp-gitops`](https://github.com/CrowdSalat/ocp-gitops)
repository points at this repo's `deploy/` directory (`gitops/bootstrap/apps-applicationset-external-manifests.yaml`).
Merging to `main` here is picked up by Argo CD automatically (within the polling interval):

```bash
# Trigger immediate evaluation of the ApplicationSets (skip ~3 min polling wait):
oc annotate applicationset external-manifests -n openshift-gitops \
  argocd.argoproj.io/refresh=hard --overwrite

# Watch the generated Application:
oc -n openshift-gitops get application booker

# Once synced, wait for the pod and check the logs:
oc -n app-medi-bucher rollout status deploy/booker
oc -n app-medi-bucher logs deploy/booker
```

## Credentials — Infisical + ExternalSecrets (never commit them)

The `booker-creds` Secret is **not** a plain manifest here — it is created by
`externalsecret.yaml` via the ExternalSecrets Operator, which syncs it from **Infisical**
(`eu.infisical.com`, project `ocp`, environment `prod`) through the cluster-wide
`infisical` ClusterSecretStore. No credential value ever lives in this repo.

**To set credentials for the first time:**
1. In Infisical (eu.infisical.com — not the US instance), add secrets for each account in
   `configmap.yaml` — key names are the *same* env var names the app uses:
   - `MEDI_CREDS_johanna_NAME` → the account login email (or a lookup on `ocp` project)
   - `MEDI_CREDS_johanna_PW` → the account password
   - one `NAME`/`PW` pair per account; the account name in the ConfigMap must match the
     `MEDI_CREDS_<NAME>_*` prefix (name uppercased).
2. The ExternalSecret refreshes every hour and creates/updates the `booker-creds` Secret.
3. To push new values immediately: `oc -n app-medi-bucher rollout restart deploy/booker` (after the
   ExternalSecret picked them up) — or simply wait for the next roll-out.

**Adding another account:** add the Infisical secrets, add the matching `data` entry in
`externalsecret.yaml`, and add the account block in `configmap.yaml`.

Missing/invalid credentials are **not** fatal: the account is suspended and logged, the
other accounts keep running.

## Verify the config (safe, no booking happens)

Dry-run performs discovery only — no Book call is ever sent:

```bash
oc -n app-medi-bucher rsh deploy/booker -- booker --dry-run /etc/booker/config.yaml
```

Expect a line per planned burst like:

```
ACCOUNT=johanna TARGET=Wirbelsäulengym CLASSID=… OPENS=2026-… STATUS=eligible
```

Real daemon log example: `ACCOUNT=johanna TARGET=… STATUS=booked`.

## One-shot runs (alternate mode, not shipped)

The Deployment runs the long-lived daemon (`booker /etc/booker/config.yaml`). If you prefer
per-window runs, create a CronJob (a small extra manifest) that uses the same
ConfigMap/Secret/PVC:

```yaml
schedule: "0 21 * * 2,4"           # adjust to your classes' release days
containers:
  - image: ghcr.io/CrowdSalat/medi-bucher:latest
    args: ["--once", "/etc/booker/config.yaml"]
    # same env/secretRefs, config volumeMount, and /app PVC mounts as deployment.yaml
```

`--once` runs one discovery + burst pass for bursts due *now* and exits — safe for
scheduled execution. Remember: once recorded in `booked_history.json` a class is never
re-booked, so overlap with the daemon is harmless.

## State / backups

`booked_history.json` is written to `/app` (container WORKDIR), backed by the `booker-history`
PVC. **It prevents re-booking a class that already has a booked record** — losing it can make
the daemon re-book. Back it up:

```bash
# snapshot the file from a running pod (deploy/booker resolves to its first pod on OpenShift 4.x;
# use the pod name from `oc -n app-medi-bucher get pods` if it doesn't)
oc -n app-medi-bucher cp deploy/booker:/app/booked_history.json ./booked_history.backup.json

# if you need to clear one entry, edit the file and copy it back
```

The PVC type depends on your cluster default storage class (`pvc.yaml` omits
`storageClassName`). If your cluster has no default, uncomment and set one there.

## Image pull

`ghcr.io/CrowdSalat/medi-bucher:latest` is a **public** GHCR package → anonymous pull works,
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
oc -n app-medi-bucher set image deploy/booker booker=ghcr.io/CrowdSalat/medi-bucher:<new-tag>
oc -n app-medi-bucher rollout status deploy/booker
```

## Security posture (restricted-v2 SCC)

- No fixed `runAsUser`/`runAsGroup` — OpenShift assigns the UID.
- `fsGroup: 0` gives the pod's gid 0 write access to the PVC and the image's group-writable `/app`.
- `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`,
  `seccompProfile: RuntimeDefault` — all restricted-v2 compatible.
- No privileged containers, no host mounts, no hostNetwork.