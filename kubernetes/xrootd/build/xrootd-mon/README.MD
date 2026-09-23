# xrootd-mon

Active monitoring agent for XRootD storage endpoints. Each pod runs a 5-minute
loop that discovers backend servers behind a redirector, issues gfal read/write/delete
transfers, and exposes the results as Prometheus metrics scraped by the cluster.

---

## Contents

- [How it works](#how-it-works)
- [Environment variables](#environment-variables)
- [Deploying with Helm (production)](#deploying-with-helm-production)
- [Running locally (development / debug)](#running-locally-development--debug)
- [Building and pushing the Docker image](#building-and-pushing-the-docker-image)
- [Grafana dashboards](#grafana-dashboards)

---

## How it works

1. **Backend discovery** — at the start of every 5-minute cycle the script finds
   the origin servers sitting behind the redirector. Two methods are supported,
   selected by `XRD_PROBE_METHOD`:
   - `xrdmapc` *(default)* — queries the redirector's internal subscriber list.
     Works for sites that expose this information externally (e.g. T2_US_Caltech).
   - `curl` — issues an HTTPS HEAD request and parses the HTTP 307 `Location:`
     header to find whichever backend the load-balancer chose. Used for sites
     where `xrdmapc` does not return usable results externally (e.g. T2_US_UCSD, T1_US_FNAL).

2. **Transfer tests** — for every discovered backend the script runs `gfal-copy`
   (write then read) and `gfal-rm` (delete) via the configured protocols.

3. **Metrics** — results are written to `/srv/xrootd-metrics` in Prometheus
   exposition format and served on port **8000** by a companion HTTP server.
   Two gauges are exposed:

   | Metric | Labels | Meaning |
   |---|---|---|
   | `xrootd_exit` | `hostname`, `mode`, `protocol`, `name` | Exit code of the last gfal/xrdmapc command (0 = success) |
   | `xrootd_runtime` | same | Wall-clock seconds the command took |

---

## Environment variables

### Mandatory

| Variable | Example | Description |
|---|---|---|
| `XRD_ENDPOINT` | `redir-08.t2-sense.ultralight.org:1094` | Redirector host:port to monitor |
| `X509_USER_PROXY` | `/tmp/x509up_u0` | Path to a valid x509 proxy file inside the container |
| `XRD_WORKDIR` | `/srv/` | Scratch directory for the temporary test file |
| `XRD_PATH` | `/store/group/tests-sense` | Remote base path; the script appends `/{year}/{month}/{day}/` automatically |
| `XRD_UNIQ_NAME` | `redir-08.t2-sense.ultralight.org` | Label used as the `name` dimension in all Prometheus metrics |
| `XRD_PROTOCOLS` | `davs` | Comma-separated protocols for gfal transfers (e.g. `davs`, `root`, `davs,root`) |
| `XRD_MODES` | `read,write,delete` | Comma-separated test modes. Valid values: `read`, `write`, `delete` |

### Optional

| Variable | Default | Description |
|---|---|---|
| `XRD_PROBE_METHOD` | `xrdmapc` | Backend discovery method: `xrdmapc` or `curl` |
| `XRD_UNIQ_WRITE` | *(unset = false)* | Any non-empty value enables unique-file-per-backend writes. Use for sites with per-origin local storage (not shared/NFS). |
| `BEARER_TOKEN` | *(unset)* | SciToken string injected into every gfal subprocess for sites that require bearer-token auth on the redirected leg. |
| `BEARER_TOKEN_FILE` | *(unset)* | Path to a file containing the SciToken (gfal2 reads this automatically). |

---

## Deploying with Helm (production)

All production deployments are managed through the Helm chart at
`kubernetes/xrootd/helm/`. **Do not set env vars by hand in production —
edit `values.yaml` instead.**

```bash
# Install or upgrade all monitoring pods:
helm upgrade --install xrootd-mon kubernetes/xrootd/helm/ -n <namespace>

# Dry-run to preview what would change:
helm upgrade --install xrootd-mon kubernetes/xrootd/helm/ -n <namespace> --dry-run
```

### Adding a new site

Append a new entry under `sites:` in `values.yaml`:

```yaml
sites:
  - name: T2_US_MyNewSite
    region: us-central          # NRP scheduling region
    affinityType: preferred     # or required
    probeMethod: curl           # omit to use the default (xrdmapc)
    path: /store/group/tests-sense
    endpoints:
      - host: redir-01.example.edu
        port: 1094
      - host: redir-02.example.edu
        port: 1094
```

Each endpoint inherits `protocols`, `modes`, `uniqWrite`, and `path` from the
site block, which inherits from `defaults`. Override any of these at the site
or endpoint level as needed.

### Key `values.yaml` knobs

| Key | Where | Description |
|---|---|---|
| `defaults.protocols` | global | Protocol(s) used for gfal calls (default: `davs`) |
| `defaults.modes` | global | Test modes (default: `read,write,delete`) |
| `defaults.uniqWrite` | global | Whether to write unique files per backend (default: `false`) |
| `site.probeMethod` | per-site | `xrdmapc` or `curl` — overrides the default for every endpoint in the site |
| `site.affinityType` | per-site | `required` (hard) or `preferred` (soft) node affinity |
| `site.zones` | per-site | Topology zone labels the pod must land on (used with `required` affinity) |
| `site.nodes` | per-site | Specific node hostnames the pod must land on (used with `required` affinity) |
| `proxy.secretName` | global | Kubernetes Secret holding the x509 proxy file |
| `bearerToken.secretName` | global | Kubernetes Secret holding the SciToken (leave empty to disable) |

---

## Running locally (development / debug)

Useful for testing a new endpoint before adding it to `values.yaml`.
You need a valid x509 proxy and the `gfal2-util-scripts` / `xrootd-client`
packages installed (or run inside the Docker image).

```bash
export XRD_ENDPOINT="redir-01.t2-sense.ultralight.org:1094"
export X509_USER_PROXY="/tmp/x509up_u0"
export XRD_PROTOCOLS="davs"
export XRD_MODES="read,write,delete"
export XRD_WORKDIR="/tmp/"
export XRD_PATH="/store/group/tests-sense"
export XRD_UNIQ_NAME="redir-01.t2-sense.ultralight.org"
# For curl-method sites (UCSD, FNAL) add:
# export XRD_PROBE_METHOD="curl"

python3 config/default/opt/xroot-mon.py
```

The script runs one full cycle and exits. Check stdout for per-command exit
codes and runtimes. Metrics are also written to `$XRD_WORKDIR/xrootd-metrics`.

---

## Building and pushing the Docker image

### Using `docker-hub-upload.sh` (recommended)

The `docker-hub-upload.sh` script in this directory handles build, tag, and push
in one step. It always targets `linux/amd64` via `docker buildx` and pushes to
the `sdnsense/xrootd-mon` repository on Docker Hub.

```bash
# From the xrootd-mon/ directory:
cd /path/to/autogole-monitoring/kubernetes/xrootd/build/xrootd-mon

# Build and push with a meaningful release tag (recommended):
bash docker-hub-upload.sh 20260922-2100
# → pushes sdnsense/xrootd-mon:20260922-2100-<today>
# → pushes sdnsense/xrootd-mon:20260922-2100   (floating tag)

# Build and push with the default 'dev' tag (for testing only):
bash docker-hub-upload.sh
# → pushes sdnsense/xrootd-mon:dev-<today>
# → pushes sdnsense/xrootd-mon:dev
```

The script will call `docker login` interactively — make sure you have push
access to `sdnsense` on Docker Hub before running.

After a successful push, update `image.tag` in `values.yaml` to the new tag
and apply the Helm upgrade:

```bash
# In values.yaml:
image:
  repository: sdnsense/xrootd-mon
  tag: "20260922-2100"   # ← update this

# Then deploy:
helm upgrade --install xrootd-mon kubernetes/xrootd/helm/ -n <namespace>
```

### Manual build (for local testing only)

```bash
# Build for the local architecture (may differ from cluster nodes — use the script for production):
docker build -t sdnsense/xrootd-mon:local-test .

# Run locally with a minimal env to verify the image starts up correctly:
docker run --rm \
  -e XRD_ENDPOINT="redir-01.t2-sense.ultralight.org:1094" \
  -e X509_USER_PROXY="/tmp/x509up_u0" \
  -e XRD_PROTOCOLS="davs" \
  -e XRD_MODES="read,write,delete" \
  -e XRD_WORKDIR="/srv/" \
  -e XRD_PATH="/store/group/tests-sense" \
  -e XRD_UNIQ_NAME="redir-01.t2-sense.ultralight.org" \
  sdnsense/xrootd-mon:local-test
```

---

## Grafana dashboards

Two dashboard JSON files are provided in `grafana-template/`:

| File | Purpose |
|---|---|
| `grafana-overview.json` | **Fleet Overview** — one tile per redirector across all sites; filter by site or redirector with dropdown variables |
| `grafana.json` | **Legacy Detail** — per-backend panels for a single redirector; useful for drilling into individual origin servers |

Import either file via **Grafana → Dashboards → Import → Upload JSON file**.

