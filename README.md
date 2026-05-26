# Server Monitoring Stack

Centralized monitoring for multi-server infrastructure using **Grafana Alloy**, **Prometheus**, **Loki**, **Alertmanager**, and **Grafana**.

## Architecture

```
Target Servers                     Monitoring Server (Docker)
┌──────────────────┐               ┌──────────────────────────────┐
│  Grafana Alloy   │               │                              │
│  ├─ CPU/RAM/Disk ├── metrics ──► │  Prometheus   :9090          │
│  ├─ Network      │               │  ├─ Alert rules              │
│  ├─ Systemd      │               │  └─► Alertmanager :9093      │
│  ├─ /var/log/*   ├── logs ─────► │  Loki          :3100         │
│  └─ Journal      │               │  Blackbox       :9115        │
└──────────────────┘               │  Grafana        :3000        │
                                   └──────────────────────────────┘
```

**Agents** run on each target server and push data to the central stack:
- **Metrics** (CPU, RAM, disk, network, systemd) are sent via Prometheus `remote_write`.
- **Logs** (syslog, auth.log, kern.log, app logs, journald) are sent to Loki.

**Monitoring server** runs five Docker containers that receive, store, visualize, and alert.

## Servers Supported

| Server Name  | Description                  |
|-------------|------------------------------|
| `manager`    | Management / control plane   |
| `receiver`   | Data ingestion service       |
| `worker`     | Background job processing    |
| `comparison` | Python FastAPI Image Comparison API (port 3000, PM2) |
| `pms-api`    | PMS API (includes app metrics on `:8050/metrics`) |
| `scraper`    | ARI scraper / subscriptions (port 8080, PM2) |

## Project Structure

```
monitoring/
├── .gitignore
├── agents/
│   ├── install-agent.sh              # Agent installer (run on each server)
│   └── alloy/
│       ├── config.alloy              # Alloy config template (single source of truth)
│       ├── config-manager.alloy      # Addon: metrics + logs (manager)
│       ├── config-receiver.alloy     # Addon: metrics + logs (receiver)
│       ├── config-worker.alloy       # Addon: metrics + logs (worker)
│       ├── config-comparison.alloy   # Addon: metrics + logs (comparison)
│       ├── config-pms-api.alloy      # Addon: app metrics (pms-api)
│       └── config-scraper.alloy      # Addon: app metrics + logs (scraper)
│
└── monitoring-server/
    ├── docker-compose.yml            # All monitoring services
    ├── .env                          # Secrets — not committed (see .env.example)
    ├── .env.example                  # Template for environment variables
    ├── prometheus/
    │   ├── prometheus.yml            # Scrape config + Blackbox probes
    │   └── rules/
    │       ├── cpu_memory.yml        # CPU and RAM alerts
    │       ├── disk.yml              # Disk usage + predictive fill alerts
    │       ├── http.yml              # HTTP endpoint + SSL cert alerts
    │       ├── network.yml           # Server down + traffic + error alerts
    │       └── security.yml          # Open FDs + system load alerts
    ├── alertmanager/
    │   └── alertmanager.yml          # Email/Slack notification routing
    ├── blackbox/
    │   └── blackbox.yml              # HTTP, ICMP, TCP probe modules
    ├── loki/
    │   └── loki-config.yml           # Log storage configuration
    └── grafana/
        └── provisioning/
            ├── datasources/
            │   └── datasources.yml   # Prometheus + Loki auto-provisioned
            └── dashboards/
                ├── dashboards.yml              # Overview + per-server folder providers
                ├── generate-server-dashboards.py
                ├── overview/
                │   ├── server-overview.json    # All servers — metrics
                │   ├── logs-overview.json      # All servers — system/auth/kernel logs
                │   └── app-logs-overview.json    # All servers — application logs (generated)
                └── servers/                    # Per-server folders (Grafana: <server>/Metrics, Logs)
                    ├── manager/
                    │   ├── metrics.json
                    │   └── logs.json
                    ├── receiver/
                    ├── worker/
                    ├── comparison/
                    ├── pms-api/
                    └── scraper/
```

## Prerequisites

**Monitoring server:**
- Docker and Docker Compose
- Ports 3000, 3100, 9090, 9093, 9115 available
- Sufficient disk space for 30 days of metrics and logs

**Pinned image versions** (`monitoring-server/docker-compose.yml`):

| Service | Image |
|---------|-------|
| Prometheus | `prom/prometheus:v3.11.3` |
| Loki | `grafana/loki:3.7.2` |
| Alertmanager | `prom/alertmanager:v0.32.1` |
| Blackbox | `prom/blackbox-exporter:v0.28.0` |
| Grafana | `grafana/grafana:13.0.1-security-01` |

**Target servers:**
- Ubuntu/Debian (the installer uses `apt`)
- Root access
- Network connectivity to the monitoring server

## Quick Start

### 1. Configure the Monitoring Server

```bash
cd monitoring-server
```

Create your `.env` file from the template and fill in real values:

```bash
cp .env.example .env
nano .env
```

Then edit the following config placeholder values:

| File | What to change |
|------|---------------|
| `.env` | Grafana password, SMTP credentials, alert email |
| `prometheus/prometheus.yml` | Replace `MANAGER_IP`, `RECEIVER_IP`, `SCRAPER_IP`, etc. with real IPs/URLs |

Start all services:

```bash
docker compose up -d
```

After upgrading images, reload configs and check health:

```bash
docker compose pull
docker compose up -d
docker compose exec prometheus promtool check rules /etc/prometheus/rules/*.yml
curl -s http://localhost:9090/-/ready
curl -s http://localhost:3100/ready
curl -s http://localhost:3000/api/health
```

Verify everything is running:

```bash
docker compose ps
```

### 2. Install Agents on Target Servers

Copy the `agents/` folder to each target server, then run:

```bash
sudo bash install-agent.sh <server-name>
```

Examples:

```bash
sudo bash install-agent.sh manager
sudo bash install-agent.sh receiver
sudo bash install-agent.sh worker
sudo bash install-agent.sh comparison
sudo bash install-agent.sh pms-api
sudo bash install-agent.sh scraper
```

Before running, edit `install-agent.sh` and set `MONITORING_IP` (line 19) to your monitoring server's IP address. The installer reads `config.alloy` from the `alloy/` directory next to the script, so make sure you copy the entire `agents/` folder.

### 3. Verify

- **Grafana:** http://MONITORING_IP:3000 (credentials from your `.env` file)
- **Prometheus:** http://MONITORING_IP:9090/targets (check agents are reporting)
- **Alertmanager:** http://MONITORING_IP:9093

Query to verify an agent is connected:

```promql
node_uname_info{server="manager"}
```

## Alert Rules

| Alert | Severity | Condition |
|-------|----------|-----------|
| HighCPU | warning | CPU > 85% for 5 min |
| CriticalCPU | critical | CPU > 95% for 2 min |
| HighMemory | warning | RAM > 85% for 5 min |
| CriticalMemory | critical | RAM > 95% for 2 min |
| DiskSpaceWarning | warning | Disk > 80% for 5 min |
| DiskSpaceCritical | critical | Disk > 90% for 2 min |
| DiskWillFillSoon | warning | Disk predicted full in < 4h |
| HTTPEndpointDown | critical | HTTP probe fails for 2 min |
| SlowHTTPResponse | warning | Response > 2s for 5 min |
| SSLCertExpiringSoon | warning | SSL cert expires in < 14 days |
| ServerDown | critical | No scrape data for 2 min |
| HighNetworkReceive | warning | > 100 MB/s for 5 min |
| NetworkErrors | warning | > 10 errors/s for 5 min |
| TooManyOpenFiles | warning | > 80% FDs used for 5 min |
| HighSystemLoad | warning | Load15 > 2x CPUs for 10 min |

## Data Collected

### Metrics (via Prometheus)
- CPU usage per core and aggregate
- Memory (total, available, buffers, cache)
- Disk usage per mount, I/O stats
- Network bytes, packets, errors per interface
- Systemd service states
- Process count and states
- System uptime and load averages

### Logs (via Loki)
- `/var/log/syslog` -- general system logs
- `/var/log/auth.log` -- SSH logins, sudo, authentication events
- `/var/log/kern.log` -- kernel messages, OOM events
- `/var/log/app/*.log` -- application logs (logfmt parsed, debug dropped)
- `journald` -- systemd journal (warnings and above only)

## Useful Commands

```bash
# Monitoring server
docker compose logs -f prometheus     # Prometheus logs
docker compose logs -f loki           # Loki logs
docker compose restart prometheus     # Restart after config change
docker compose exec prometheus promtool check rules /etc/prometheus/rules/*.yml

# Target servers
systemctl status alloy                # Agent status
journalctl -u alloy -f               # Agent logs
systemctl restart alloy               # Restart after config change
```

## Retention

| Data | Retention |
|------|-----------|
| Metrics (Prometheus) | 30 days |
| Logs (Loki) | 30 days |

## Configuration Reference

### Adding a New Server

1. Run `install-agent.sh <new-server-name>` on the target server.
2. Optionally add HTTP probe targets in `prometheus/prometheus.yml`.
3. The server will appear automatically in Grafana queries using the `server` label.

### Adding App Metrics

If your application exposes a `/metrics` endpoint (Prometheus format), create a `config-<server-name>.alloy` addon file in `agents/alloy/`. The installer auto-appends it when the server name matches (e.g. `config-pms-api.alloy`, `config-comparison.alloy`). Adjust ports and log paths in the addon as needed.

### Comparison server (Python FastAPI)

The comparison addon (`config-comparison.alloy`) scrapes **`GET /metrics` on port 3000** (queue/worker gauges from the app) and ships **PM2 logs** to Loki.

| What | How |
|------|-----|
| **System metrics** (CPU, RAM, disk, network) | Alloy `prometheus.exporter.unix` in base `config.alloy` → `remote_write` (label `server="comparison"`) |
| **App metrics** (`comparison_queue_size`, `comparison_active_workers`, …) | `prometheus.scrape` in `config-comparison.alloy` → same `remote_write` |
| **HTTP uptime** | Blackbox: `http://COMPARISON_IP:3000/health` in `prometheus/prometheus.yml` (open port 3000 from the monitoring host if probes stay DOWN) |
| **Application logs** | PM2 logs + Python parsing → Loki (`job="comparison"`, `host="comparison"`) |

| Item | Value |
|------|-------|
| App port | `3000` |
| Grafana | Open the server folder (e.g. **comparison**) → **Metrics** or **Logs**; all servers: **Server Overview** |
| Logs | `/root/.pm2/logs/` or `/home/<user>/.pm2/logs/` (`comparison-out.log`, `comparison-error.log`) |
| Deploy path | `/var/www/comparison` (PM2 name: `comparison`) |

**Per-route HTTP metrics** (like manager’s `http_request_duration_seconds_*`) require extra instrumentation in the FastAPI app (e.g. `prometheus-fastapi-instrumentator`). The comparison app currently exposes queue/worker gauges only.

### Scraper server (Node.js Express)

The scraper addon (`config-scraper.alloy`) scrapes **`GET /metrics` on port 8080** (express-prom-bundle in `app.js`) and ships **PM2 logs** to Loki.

| Item | Value |
|------|-------|
| App port | `8080` |
| PM2 name | `scraper` |
| Deploy path | `/var/www/scraper` |
| Grafana | **scraper** → **Metrics** / **Logs** |
| Logs | `scraper-out.log`, `scraper-error.log` under `/root/.pm2/logs/` |
| HTTP uptime | Blackbox: `http://SCRAPER_IP:8080/` in `prometheus/prometheus.yml` |

**Comparison logs missing in Grafana:** The **All Servers — Application Logs** dashboard queries `job=~"app|comparison|manager|receiver|worker|pms-api|scraper"`. PM2 logs under `/root/.pm2/logs/` are not readable by the `alloy` user until traverse permissions are set (`/root` is mode `700` by default). On the comparison server:

```bash
sudo bash grant-alloy-access.sh comparison   # PM2 + Docker + base log path checks
systemctl restart alloy
```

If `head` still fails, install ACL tools (`apt install acl`) and re-run `grant-alloy-access.sh`.

### Loki container unhealthy

Grafana may report `dependency loki failed to start` when Loki never becomes **healthy** (not necessarily when the process crashes).

**Loki 3.6+** images are distroless: they have no `wget`, `curl`, or shell, so a `CMD-SHELL` healthcheck with `wget` always fails. This stack uses the built-in check: `loki -health` in `docker-compose.yml`.

Loki 3.x also requires the `common:` storage block. Older `tsdb_shipper` / `shared_store` settings cause Loki to crash and mark the container unhealthy.

```bash
cd monitoring-server
docker compose logs loki --tail 50
docker compose up -d loki
curl http://localhost:3100/ready
docker inspect loki --format '{{.State.Health.Status}}'
```

If errors persist after upgrading from Loki 2.x, reset the volume (deletes stored logs):

```bash
docker compose down
docker volume rm monitoring-server_loki_data
docker compose up -d
```

### Loki logs: `failed to get token ranges for ingester` / `zone not set`

Loki **3.1.x** logs this periodically on single-node setups. It is a known bug in stream-ownership recalculation ([grafana/loki#13414](https://github.com/grafana/loki/issues/13414)); ingestion and queries usually still work. This stack uses **Loki 3.2+** (currently **3.7.2**), which removes the noisy code path. `pattern_ingester` is disabled in `loki-config.yml` for single-node setups. After pulling changes:

```bash
cd monitoring-server
docker compose pull loki
docker compose up -d loki
```

### Customizing Alerts

Edit the YAML files in `prometheus/rules/`. After changes, reload Prometheus:

```bash
curl -X POST http://localhost:9090/-/reload
```
