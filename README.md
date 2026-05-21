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
| `comparison` | Comparison engine            |
| `pms-api`    | PMS API (includes app metrics on `:8080/metrics`) |

## Project Structure

```
monitoring/
├── .gitignore
├── agents/
│   ├── install-agent.sh              # Agent installer (run on each server)
│   └── alloy/
│       ├── config.alloy              # Alloy config template (single source of truth)
│       └── config-pms-api.alloy      # App metrics scrape block (auto-appended for pms-api)
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
                ├── dashboards.yml        # Dashboard provider config
                ├── server-overview.json  # Server metrics dashboard
                └── logs-overview.json    # Logs viewer dashboard
```

## Prerequisites

**Monitoring server:**
- Docker and Docker Compose
- Ports 3000, 3100, 9090, 9093, 9115 available
- Sufficient disk space for 30 days of metrics and logs

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
| `prometheus/prometheus.yml` | Replace `MANAGER_IP`, `RECEIVER_IP`, etc. with real IPs/URLs |

Start all services:

```bash
docker compose up -d
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

If your application exposes a `/metrics` endpoint (Prometheus format), append the content of `config-pms-api.alloy` to the server's Alloy config at `/etc/alloy/config.alloy`, adjusting the port as needed.

### Customizing Alerts

Edit the YAML files in `prometheus/rules/`. After changes, reload Prometheus:

```bash
curl -X POST http://localhost:9090/-/reload
```
