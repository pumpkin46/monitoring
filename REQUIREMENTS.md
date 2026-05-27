# Monitoring Stack — Implementation Requirements

This document defines what is already implemented, what must be done for production, and optional enhancements for the centralized monitoring stack (Grafana Alloy, Prometheus, Loki, Alertmanager, Grafana).

**Servers:** `manager`, `receiver`, `worker`, `comparison`, `pms-api`, `scraper`

---

## Architecture (reference)

```
Target Servers                     Monitoring Server (Docker)
┌──────────────────┐               ┌──────────────────────────────┐
│  Grafana Alloy   │               │  Prometheus   :9090          │
│  ├─ CPU/RAM/Disk ├── metrics ──► │  Loki          :3100         │
│  ├─ Network      │  remote_write │  Blackbox      :9115        │
│  ├─ Systemd      │               │  Alertmanager  :9093        │
│  ├─ /var/log/*   ├── logs ─────► │  Grafana       :3000        │
│  └─ Journal      │               └──────────────────────────────┘
└──────────────────┘
```

---

## Status legend

| Status | Meaning |
|--------|---------|
| Done | Implemented in this repository |
| Required | Must be completed for production |
| Recommended | Improves operations; not blocking go-live |
| Optional | Nice-to-have or application-team work |

---

## 1. Already implemented (Done)

### 1.1 Monitoring server

| ID | Item | Location |
|----|------|----------|
| D-01 | Docker Compose stack (Prometheus 3.x, Loki 3.7, Alertmanager, Blackbox, Grafana 13) | `monitoring-server/docker-compose.yml` |
| D-02 | 30-day retention for metrics and logs | `docker-compose.yml`, `loki-config.yml` |
| D-03 | Prometheus remote-write receiver for Alloy agents | `docker-compose.yml` (`--web.enable-remote-write-receiver`) |
| D-04 | Alert rules: CPU/RAM, disk, HTTP/SSL, network, security | `monitoring-server/prometheus/rules/` |
| D-05 | Alertmanager email routing with severity grouping | `monitoring-server/alertmanager/alertmanager.yml` |
| D-06 | Blackbox HTTP and TLS probe modules | `monitoring-server/blackbox/blackbox.yml` |
| D-07 | Grafana datasources (Prometheus + Loki) | `monitoring-server/grafana/provisioning/datasources/` |
| D-08 | Dashboard provisioning: Overview + per-server folders | `monitoring-server/grafana/provisioning/dashboards/dashboards.yml` |
| D-09 | Dashboard generator script | `generate-server-dashboards.py` |
| D-10 | CI/CD deploy workflow | `.github/workflows/deploy.yml` |

### 1.2 Agents

| ID | Item | Location |
|----|------|----------|
| D-11 | Base Alloy config (unix exporter, remote_write, logs) | `agents/alloy/config.alloy` |
| D-12 | Per-server addons (all six servers) | `agents/alloy/config-<server>.alloy` |
| D-13 | Agent installer | `agents/install-agent.sh` |
| D-14 | Config-only sync (CD) | `agents/sync-config.sh` |
| D-15 | PM2/Docker log permission helper | `agents/grant-alloy-access.sh` |
| D-16 | Agent uninstaller | `agents/uninstall-agent.sh` |

### 1.3 Grafana dashboards

| ID | Item | Location |
|----|------|----------|
| D-17 | All-servers metrics overview | `overview/server-overview.json` |
| D-18 | All-servers system/auth/kernel logs | `overview/logs-overview.json` |
| D-19 | All-servers application logs | `overview/app-logs-overview.json` |
| D-20 | Per-server Metrics + Logs (×6) | `servers/<name>/metrics.json`, `logs.json` |
| D-21 | Receiver integration/channel panels | Generated via `generate-server-dashboards.py` |

---

## 2. Production requirements (P0 — Required)

These items block a correct production deployment.

### R-01 — Monitoring server environment

| Field | Value |
|-------|-------|
| **Status** | Required |
| **Owner** | Ops / monitoring host |

Create `monitoring-server/.env` from `.env.example` and set:

- `GRAFANA_ADMIN_PASSWORD`
- `SMTP_HOST`, `SMTP_FROM`, `SMTP_PASSWORD`
- `ALERT_EMAIL_TO`

**Acceptance criteria:**

- `docker compose up -d` completes with all services healthy
- Grafana login works
- Alertmanager starts without SMTP template errors

---

### R-02 — Install Alloy on all target servers

| Field | Value |
|-------|-------|
| **Status** | Required |
| **Owner** | Per-server admin (one-time) |

On each server:

```bash
sudo bash install-agent.sh <server-name>
```

Set `MONITORING_IP` in `install-agent.sh` (or use GitHub Actions `sync-config.sh` after initial install).

**Acceptance criteria:**

- `systemctl is-active alloy` on all six servers
- PromQL: `node_uname_info{server="<name>"}` returns a series for each server

---

### R-03 — Fix ServerDown alert for remote_write

| Field | Value |
|-------|-------|
| **Status** | Done |
| **Owner** | Monitoring repo |
| **File** | `monitoring-server/prometheus/rules/network.yml` |

**Problem:** Agents push metrics via `remote_write`. They are not Prometheus scrape targets, so `up == 0` does not detect agent/server failure.

**Requirement:** Replace or supplement `ServerDown` with staleness detection on host metrics, for example:

```promql
absent_over_time(node_boot_time_seconds[3m])
```

or per-server:

```promql
absent_over_time(node_boot_time_seconds{server="manager"}[3m])
```

**Acceptance criteria:**

- Stopping Alloy on a server fires `ServerDown` (or renamed equivalent) within ~3 minutes
- Blackbox `probe_success` failures still trigger `HTTPEndpointDown` independently

---

### R-04 — Complete Blackbox HTTP probe targets

| Field | Value |
|-------|-------|
| **Status** | Done |
| **Owner** | Monitoring repo |
| **File** | `monitoring-server/prometheus/prometheus.yml` |

**Was:** Worker and scraper had no HTTP probes; comparison probe lacked `:3000/health`.

**Required probe URLs:**

| Server | Suggested probe URL |
|--------|---------------------|
| manager | `http://<MANAGER_IP>:9000/` or public URL |
| receiver | `https://ota-receiver.localota.stream/healthz` |
| worker | `http://<WORKER_IP>:8001/` |
| comparison | `http://<COMPARISON_IP>:3000/health` |
| pms-api | `https://gha.localota.stream` (confirm correct health path) |
| scraper | `http://<SCRAPER_IP>:8080/` |

**Acceptance criteria:**

- All six servers appear in Prometheus with `probe_success` metrics
- `HTTPEndpointDown` and SSL alerts apply to HTTPS targets

---

### R-05 — Fix Grafana HTTP probe panels (worker, scraper)

| Field | Value |
|-------|-------|
| **Status** | Done |
| **Owner** | Monitoring repo |
| **Files** | `generate-server-dashboards.py`, regenerated `servers/*/metrics.json` |

**Was:** Worker and scraper dashboards used placeholder regexes `WORKER` and `SCRAPER` in `probe_match`.

**Requirement:**

1. Add real `probe_match` values in `SERVERS` in `generate-server-dashboards.py`
2. Run: `python3 generate-server-dashboards.py`
3. Commit regenerated JSON

**Acceptance criteria:**

- Worker and scraper Metrics dashboards show HTTP Up and Response Time panels with data

---

### R-06 — PM2 log permissions (app servers)

| Field | Value |
|-------|-------|
| **Status** | Required |
| **Owner** | Per-server admin |
| **Servers** | manager, worker, comparison, pms-api, scraper |

Run on each affected host:

```bash
sudo bash grant-alloy-access.sh <server-name>
systemctl restart alloy
```

**Acceptance criteria:**

- Loki query `{job=~"<server>|app", host="<server>"}` returns PM2 log lines in Grafana

---

### R-07 — Receiver Docker access

| Field | Value |
|-------|-------|
| **Status** | Required |
| **Owner** | Receiver server admin |

```bash
sudo bash grant-alloy-access.sh receiver
systemctl restart alloy
```

**Acceptance criteria:**

- Integration metrics (`integration` label) visible in Prometheus
- Receiver container logs visible in Loki/Grafana

---

## 3. Operational requirements (P1 — Recommended)

### R-08 — Verify Alertmanager email delivery

| Field | Value |
|-------|-------|
| **Status** | Recommended |

Send a test alert (e.g. stop Alloy briefly or use Alertmanager UI). Confirm firing and resolved emails arrive with correct subject and HTML body.

---

### R-09 — Slack notifications (optional)

| Field | Value |
|-------|-------|
| **Status** | Optional |

Uncomment and configure Slack receiver in `alertmanager.yml` if the team uses Slack.

---

### R-10 — Firewall and network security

| Field | Value |
|-------|-------|
| **Status** | Recommended |

| Port | Service | Recommendation |
|------|---------|----------------|
| 9090 | Prometheus (remote write) | Allow only from agent server IPs |
| 3100 | Loki (push) | Allow only from agent server IPs |
| 3000 | Grafana | Restrict to VPN / admin IPs |
| 9093, 9115 | Alertmanager, Blackbox | Already bound to localhost in compose |

Agent outbound: allow to monitoring server on 9090 and 3100 (`install-agent.sh` adds UFW rules if UFW is active).

---

### R-11 — GitHub Actions secrets

| Field | Value |
|-------|-------|
| **Status** | Recommended |

Configure in GitHub (repository or `production` environment):

| Secret | Purpose |
|--------|---------|
| `PRIVATEKEY` | SSH private key |
| `DEPLOY_USER` | SSH user |
| `DEPLOY_MONITORING_HOST` | Monitoring server IP/hostname |
| `MONITORING_ENV_FILE` | Full contents of `monitoring-server/.env` |
| `DEPLOY_MANAGER_HOST` | Agent: manager |
| `DEPLOY_RECEIVER_HOST` | Agent: receiver |
| `DEPLOY_WORKER_HOST` | Agent: worker |
| `DEPLOY_COMPARISON_HOST` | Agent: comparison |
| `DEPLOY_PMS_API_HOST` | Agent: pms-api |
| `DEPLOY_SCRAPER_HOST` | Agent: scraper |

**Acceptance criteria:**

- Push to `main` deploys changed paths (`monitoring-server/` or `agents/`)
- Health-check job passes after monitoring-server deploy

---

### R-12 — One-time server preparation

| Field | Value |
|-------|-------|
| **Status** | Recommended |

**Monitoring host:**

- Clone repo to `/var/www/monitoring`
- Create `.env` locally once (CD overwrites from `MONITORING_ENV_FILE` secret)

**Agent hosts:**

- Run `install-agent.sh <server-name>` once before relying on CD-only `sync-config.sh`

---

### R-13 — Regenerate dashboards after config changes

| Field | Value |
|-------|-------|
| **Status** | Recommended |

Whenever `probe_match`, server list, or panel features change:

```bash
cd monitoring-server/grafana/provisioning/dashboards
python3 generate-server-dashboards.py
```

Commit `overview/app-logs-overview.json` and `servers/**/*.json`.

---

### R-14 — Remove legacy dashboard files

| Field | Value |
|-------|-------|
| **Status** | Done |

Orphan files not referenced by `dashboards.yml` (removed by generator / manual cleanup):

- `monitoring-server/grafana/provisioning/dashboards/server-overview.json`
- `monitoring-server/grafana/provisioning/dashboards/logs-overview.json`
- `monitoring-server/grafana/provisioning/dashboards/comparison/` (if duplicated under `servers/comparison/`)

Remove or consolidate into `overview/` to avoid confusion.

---

### R-15 — Update README verification steps

| Field | Value |
|-------|-------|
| **Status** | Done |

**Was:** README suggested checking Prometheus `/targets` for agents. Agents use **remote write**, not scrape targets.

**Requirement:** Document verification via:

- PromQL: `node_uname_info{server="<name>"}`
- Prometheus → Status → Remote Write (if applicable)
- Grafana Overview dashboard

---

## 4. Enhancements (P2 — Optional)

### R-16 — Per-route HTTP metrics for comparison (FastAPI)

| Field | Value |
|-------|-------|
| **Status** | Optional |
| **Owner** | Comparison application repo |

Comparison currently exposes queue/worker gauges only. For route-level latency (like manager’s `http_request_duration_seconds_*`), add instrumentation (e.g. `prometheus-fastapi-instrumentator`) in the FastAPI app.

---

### R-17 — MySQL monitoring

| Field | Value |
|-------|-------|
| **Status** | Optional |

Uncomment `mysql` job in `prometheus.yml` and deploy `mysqld_exporter` on the database host if needed.

---

### R-18 — Redis monitoring

| Field | Value |
|-------|-------|
| **Status** | Optional |

Uncomment `redis` job in `prometheus.yml` and deploy `redis_exporter` if needed.

---

### R-19 — Grafana unified alerting

| Field | Value |
|-------|-------|
| **Status** | Optional |

`GF_UNIFIED_ALERTING_ENABLED=true` is set, but no Grafana alert rules are provisioned. Either:

- Continue using Prometheus → Alertmanager only (current design), or
- Add Grafana alert rule provisioning under `grafana/provisioning/`

---

### R-20 — Backup and disaster recovery

| Field | Value |
|-------|-------|
| **Status** | Optional |

Define backup procedure for Docker volumes:

- `prometheus_data`
- `loki_data`
- `grafana_data`
- `alertmanager_data`

Document restore steps and retention alignment (30 days).

---

### R-21 — TLS / authentication for remote write and Loki push

| Field | Value |
|-------|-------|
| **Status** | Optional |

Remote write and Loki push use plain HTTP. Acceptable on a private network only. If exposed, add reverse proxy with TLS and authentication.

---

### R-22 — Comparison PM2 paths for non-root deploy user

| Field | Value |
|-------|-------|
| **Status** | Optional |
| **File** | `agents/alloy/config-comparison.alloy` |

If PM2 runs as a deploy user (not root), add `/home/<user>/.pm2/logs/comparison-*.log` paths alongside `/root/.pm2/logs/`.

---

## 5. Application requirements (P3 — Outside this repo)

Application teams must ensure the following for full observability.

| ID | Requirement | Servers |
|----|-------------|---------|
| A-01 | Expose Prometheus `/metrics` endpoint | manager, worker, receiver, pms-api, scraper, comparison |
| A-02 | Health endpoint reachable from monitoring host (for Blackbox) | All |
| A-03 | Logs parseable by Alloy pipelines (PM2 format, logfmt, or documented patterns) | All app servers |
| A-04 | Receiver: Docker Compose with published ports 8001–8012 per integration | receiver |

---

## 6. Alert rules reference

Implemented in `monitoring-server/prometheus/rules/`:

| Alert | Severity | Condition |
|-------|----------|-----------|
| HighCPU | warning | CPU > 85% for 5 min |
| CriticalCPU | critical | CPU > 95% for 2 min |
| HighMemory | warning | RAM > 85% for 5 min |
| CriticalMemory | critical | RAM > 95% for 2 min |
| DiskSpaceWarning | warning | Disk > 80% for 5 min |
| DiskSpaceCritical | critical | Disk > 90% for 5 min |
| DiskWillFillSoon | warning | Disk predicted full in < 4 h |
| HTTPEndpointDown | critical | HTTP probe fails for 2 min |
| SlowHTTPResponse | warning | Response > 2 s for 5 min |
| SSLCertExpiringSoon | warning | SSL cert expires in < 14 days |
| ServerDown | critical | No host metrics via remote_write for 3+ min (staleness on `node_boot_time_seconds`) |
| HighNetworkReceive | warning | > 100 MB/s for 5 min |
| NetworkErrors | warning | > 10 errors/s for 5 min |
| TooManyOpenFiles | warning | > 80% FDs used for 5 min |
| HighSystemLoad | warning | Load15 > 2× CPUs for 10 min |

---

## 7. Implementation phases

### Phase 1 — Go live (P0)

| Order | Requirement |
|-------|-------------|
| 1 | R-01 — `.env` and start Docker stack |
| 2 | R-04 — Blackbox probes in `prometheus.yml` |
| 3 | R-03 — Fix `ServerDown` alert |
| 4 | R-02 — Install Alloy on all servers |
| 5 | R-06, R-07 — `grant-alloy-access.sh` |
| 6 | R-05 — Regenerate Grafana dashboards |
| 7 | Verify metrics, logs, and test alerts |

### Phase 2 — Harden (P1)

| Order | Requirement |
|-------|-------------|
| 1 | R-08 — Email alert test |
| 2 | R-10 — Firewall rules |
| 3 | R-11, R-12 — CI secrets and server prep |
| 4 | R-14, R-15 — Cleanup legacy files and docs |

### Phase 3 — Extend (P2 / P3)

Optional items R-16 through R-22 and application requirements A-01 through A-04.

---

## 8. Deployment checklist

Use this checklist when rolling out or auditing production:

- [ ] `monitoring-server/.env` configured (R-01)
- [ ] `docker compose up -d` — all services healthy
- [ ] `promtool check rules` passes
- [x] Blackbox probes cover all six servers (R-04)
- [x] `ServerDown` uses remote_write–compatible expression (R-03)
- [ ] Alloy installed and active on each server (R-02)
- [ ] `grant-alloy-access.sh` run where needed (R-06, R-07)
- [ ] PromQL `node_uname_info{server="..."}` OK per server
- [ ] Grafana Overview and per-server dashboards show data
- [ ] Loki logs visible for app servers
- [ ] Test alert email received (R-08)
- [ ] GitHub Actions secrets configured (R-11)
- [x] Dashboards regenerated and committed after probe changes (R-05, R-13)

---

## 9. Verification queries

**Metrics (Prometheus):**

```promql
node_uname_info{server="manager"}
probe_success
comparison_active_workers{server="comparison"}
http_request_duration_seconds_count{server="receiver"}
```

**Logs (Loki in Grafana Explore):**

```logql
{host="manager", job=~"manager|app"}
{host="receiver", job="receiver"}
{host="comparison", job="comparison"}
```

**Health URLs (monitoring host):**

- Grafana: `http://<MONITORING_IP>:3000/api/health`
- Prometheus: `http://<MONITORING_IP>:9090/-/ready`
- Loki: `http://<MONITORING_IP>:3100/ready`

---

## 10. Related documentation

- [README.md](README.md) — Architecture, quick start, server-specific notes
- [monitoring-server/.env.example](monitoring-server/.env.example) — Environment variables
- [.github/workflows/deploy.yml](.github/workflows/deploy.yml) — CI/CD pipeline

---

*Last updated: 2026-05-26*
