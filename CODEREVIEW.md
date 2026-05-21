# Code Review — Monitoring Project

**Date:** 2026-05-20  
**Files reviewed:** 19 files across `agents/` and `monitoring-server/`

---

## Summary


| Severity | Count |
| -------- | ----- |
| Critical | 5     |
| High     | 5     |
| Medium   | 6     |
| Low      | 7     |


---

## Critical

### 1. GPG key download is broken — `install-agent.sh` line 48

**File:** `agents/install-agent.sh`

```bash
# Current (broken)
wget -q -O /etc/apt/keyrings/grafana.gpg \
  https://apt.grafana.com/gpg.key
```

The Grafana GPG key is in ASCII-armored format but the file is saved as `.gpg` (binary). `apt` will fail signature verification.

**Fix:**

```bash
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
```

---

### 2. APT sources.list line is malformed — `install-agent.sh` line 51

**File:** `agents/install-agent.sh`

```bash
# Current (broken)
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] \
  https://apt.grafana.com stable main" \
  | tee /etc/apt/sources.list.d/grafana.list
```

The backslash-continuation inside double quotes embeds a literal newline and leading spaces into the `.list` file. `apt` cannot parse this.

**Fix:**

```bash
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
  | tee /etc/apt/sources.list.d/grafana.list
```

---

### 3. Hardcoded credentials in version-controlled files

**Files:**

- `monitoring-server/docker-compose.yml` line 85 — Grafana admin password
- `monitoring-server/alertmanager/alertmanager.yml` line 5 — SMTP password

```yaml
# docker-compose.yml
- GF_SECURITY_ADMIN_PASSWORD=StrongPassword123!

# alertmanager.yml
smtp_auth_password: "your-email-password"
```

Secrets should never be committed to source control.

**Fix:** Use a `.env` file (excluded from git) and reference variables:

```yaml
# docker-compose.yml
- GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}

# alertmanager.yml
smtp_auth_password: "${SMTP_PASSWORD}"
```

Create `.env.example` with placeholder values for documentation.

---

### 4. Loki uses deprecated schema v11 and boltdb-shipper — `loki-config.yml` line 19-21

**File:** `monitoring-server/loki/loki-config.yml`

```yaml
store: boltdb-shipper
schema: v11
```

`boltdb-shipper` and schema `v11` are deprecated in Loki 2.9.x. They will be removed in future releases.

**Fix:**

```yaml
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

---

### 5. `table_manager` is deprecated and retention doesn't work — `loki-config.yml` lines 42-44

**File:** `monitoring-server/loki/loki-config.yml`

```yaml
table_manager:
  retention_deletes_enabled: true
  retention_period: 30d
```

The `table_manager` component is a no-op in newer Loki versions. Logs will accumulate indefinitely.

**Fix:** Replace with a `compactor` block:

```yaml
compactor:
  working_directory: /loki/compactor
  shared_store: filesystem
  retention_enabled: true
  compaction_interval: 10m
  retention_delete_delay: 2h
  retention_delete_worker_count: 150
```

Remove the `table_manager` block entirely.

---

## High

### 6. CPU alert expression is misleading — `cpu_memory.yml` line 7

**File:** `monitoring-server/prometheus/rules/cpu_memory.yml`

```yaml
expr: 100 - (avg by(server) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 85
```

The parenthesization is confusing. While mathematically it works, the `* 100` being grouped with the inner expression makes it hard to reason about. The same issue exists on line 17 for `CriticalCPU`.

**Fix:** Use the clearer standard form:

```yaml
expr: (1 - avg by(server)(rate(node_cpu_seconds_total{mode="idle"}[5m]))) * 100 > 85
```

---

### 7. `HighSystemLoad` expression is fragile — `security.yml` line 23

**File:** `monitoring-server/prometheus/rules/security.yml`

```yaml
expr: node_load15 / count without(cpu, mode) (node_cpu_seconds_total{mode="idle"}) > 2
```

`count without(cpu, mode)` depends on the exact set of labels present. If Alloy adds extra labels or relabeling changes the label set, the count will be incorrect.

**Fix:**

```yaml
expr: node_load15 / count by(server, instance)(node_cpu_seconds_total{mode="idle"}) > 2
```

---

### 8. No Loki compactor — retention is non-functional

**File:** `monitoring-server/loki/loki-config.yml`

Without a `compactor` block, Loki has no mechanism to delete old data. The `retention_period: 30d` in `limits_config` is ignored unless a compactor is configured.

**Fix:** See item #5 above.

---

### 9. Five empty placeholder config files serve no purpose

**Files:**

- `agents/alloy/config-manager.alloy`
- `agents/alloy/config-receiver.alloy`
- `agents/alloy/config-worker.alloy`
- `agents/alloy/config-scraper.alloy`
- `agents/alloy/config-comparison.alloy`

**Fix:** Either populate them with actual per-server configs and have the installer use them, or delete them entirely.These are all empty (1 line, no content). The installer script (`install-agent.sh`) generates config inline and does not reference these files. They create confusion about how deployment works.



---

### 10. Config duplication between template and installer creates drift risk

**Files:**

- `agents/alloy/config.alloy` — standalone template (158 lines)
- `agents/install-agent.sh` — inline config (lines 87–244)

The two copies are already slightly different:

- The template includes `request_id` label extraction; the installer does not.
- The template has more detailed comments.

Any future edit must be applied to both places.

**Fix:** Have the installer copy and `sed`-replace the template file instead of embedding config inline:

```bash
cp /path/to/config.alloy /etc/alloy/config.alloy
sed -i "s/SERVER_NAME/${SERVER_NAME}/g" /etc/alloy/config.alloy
sed -i "s/MONITORING_IP/${MONITORING_IP}/g" /etc/alloy/config.alloy
```

---

## Medium

### 11. Blackbox exporter has no config file — `docker-compose.yml` line 63

**File:** `monitoring-server/docker-compose.yml`

```yaml
blackbox:
  image: prom/blackbox-exporter:v0.25.0
  # no config volume
```

Uses the built-in default config which only supports basic `http_2xx`. Cannot customize for ICMP, TCP, custom headers, or authentication.

**Fix:** Create `monitoring-server/blackbox/blackbox.yml` and mount it:

```yaml
volumes:
  - ./blackbox/blackbox.yml:/etc/blackbox_exporter/config.yml
```

---

### 12. No Docker health checks on any service

**File:** `monitoring-server/docker-compose.yml`

None of the five services have `healthcheck` definitions. A container can be "running" but the application inside may not be ready.

**Fix:** Add health checks, for example:

```yaml
prometheus:
  healthcheck:
    test: ["CMD", "wget", "--spider", "-q", "http://localhost:9090/-/healthy"]
    interval: 15s
    timeout: 5s
    retries: 3

loki:
  healthcheck:
    test: ["CMD", "wget", "--spider", "-q", "http://localhost:3100/ready"]
    interval: 15s
    timeout: 5s
    retries: 3
```

---

### 13. Overly aggressive remote_write queue config — `config.alloy` line 42-46

**File:** `agents/alloy/config.alloy`

```
queue_config {
  capacity             = 2500
  max_shards           = 200
  max_samples_per_send = 500
}
```

`max_shards: 200` allows up to 200 parallel HTTP connections from a single agent. This is excessive for a single server and could overwhelm both the agent and Prometheus.

**Fix:** Use conservative values:

```
queue_config {
  capacity             = 2500
  max_shards           = 10
  max_samples_per_send = 500
}
```

---

### 14. Alert emails use plain text in HTML field — `alertmanager.yml` line 35

**File:** `monitoring-server/alertmanager/alertmanager.yml`

```yaml
html: |
  {{ range .Alerts }}
  Alert: {{ .Annotations.summary }}
  Details: {{ .Annotations.description }}
  Since: {{ .StartsAt }}
  {{ end }}
```

The `html` field expects HTML content but contains plain text. Most email clients will render this as a single collapsed line.

**Fix:** Either switch to `text` field or use proper HTML:

```yaml
html: |
  <h2>Monitoring Alert</h2>
  <table border="1" cellpadding="8" cellspacing="0">
    <tr><th>Alert</th><th>Details</th><th>Since</th></tr>
    {{ range .Alerts }}
    <tr>
      <td>{{ .Annotations.summary }}</td>
      <td>{{ .Annotations.description }}</td>
      <td>{{ .StartsAt.Format "2006-01-02 15:04:05" }}</td>
    </tr>
    {{ end }}
  </table>
```

---

### 15. All services exposed on 0.0.0.0 — `docker-compose.yml`

**File:** `monitoring-server/docker-compose.yml`

```yaml
ports:
  - "9090:9090"    # Prometheus
  - "3100:3100"    # Loki
  - "9093:9093"    # Alertmanager
  - "9115:9115"    # Blackbox
  - "3000:3000"    # Grafana
```

All services are accessible from any network interface. Internal services should not be publicly reachable.

**Fix:** Bind internal services to localhost and only expose Grafana externally:

```yaml
prometheus:
  ports:
    - "9090:9090"          # needs external access for remote_write from agents

loki:
  ports:
    - "3100:3100"          # needs external access for log push from agents

alertmanager:
  ports:
    - "127.0.0.1:9093:9093"   # internal only

blackbox:
  ports:
    - "127.0.0.1:9115:9115"   # internal only

grafana:
  ports:
    - "3000:3000"              # external — user-facing
```

---

### 16. No `.env` file or `.env.example`

The project has no way to configure environment-specific values (IPs, credentials, email settings) without editing source files directly.

**Fix:** Create `.env.example`:

```env
MONITORING_IP=192.168.1.100
GRAFANA_ADMIN_PASSWORD=changeme
SMTP_HOST=smtp.gmail.com:587
SMTP_FROM=alerts@yourcompany.com
SMTP_PASSWORD=changeme
ALERT_EMAIL_TO=team@yourcompany.com
```

---

## Low

### 17. No Grafana dashboard JSON files

**File:** `monitoring-server/grafana/provisioning/dashboards/dashboards.yml`

The dashboard provider is configured but no `.json` dashboard files exist. Grafana starts with an empty dashboard list.

**Suggestion:** Add at least a Node Exporter Full dashboard (Grafana ID 1860) and a Loki log viewer dashboard.

---

### 18. No `.gitignore` file

Sensitive data and runtime files could be accidentally committed.

**Suggestion:** Create `.gitignore`:

```
.env
*.data
grafana_data/
prometheus_data/
loki_data/
alertmanager_data/
```

---

### 19. Docker Compose `version: "3.8"` is deprecated

**File:** `monitoring-server/docker-compose.yml` line 1

Modern Docker Compose (v2+) ignores the `version` field and emits a warning.

**Suggestion:** Remove line 1 entirely.

---

### 20. No `depends_on` between services

**File:** `monitoring-server/docker-compose.yml`

Grafana may start before Prometheus and Loki are ready, causing initial datasource errors.

**Suggestion:**

```yaml
grafana:
  depends_on:
    prometheus:
      condition: service_healthy
    loki:
      condition: service_healthy
```

(Requires health checks from item #12.)

---

### 21. Loki `chunk_retain_period` is very short — `loki-config.yml` line 14

**File:** `monitoring-server/loki/loki-config.yml`

```yaml
chunk_retain_period: 30s
```

A 30-second retain period increases the risk of log data loss during restarts.

**Suggestion:** Increase to `2m`.

---

### 22. Image versions are outdated

**File:** `monitoring-server/docker-compose.yml`


| Image                  | Current | Latest Available |
| ---------------------- | ------- | ---------------- |
| prom/prometheus        | v2.51.0 | v2.54+           |
| grafana/loki           | 2.9.5   | 3.x              |
| prom/alertmanager      | v0.27.0 | v0.28+           |
| grafana/grafana        | 10.4.2  | 11.x             |
| prom/blackbox-exporter | v0.25.0 | v0.26+           |


**Suggestion:** Update to latest stable versions, especially Loki 3.x which has significant improvements.

---

### 23. Missing `NetworkErrors` description — `network.yml` line 36

**File:** `monitoring-server/prometheus/rules/network.yml`

The `NetworkErrors` alert has a `summary` annotation but is missing the `description` annotation, unlike every other alert rule in the project.

**Suggestion:**

```yaml
annotations:
  summary: "Network errors on {{ $labels.server }}"
  description: "{{ $value | printf \"%.1f\" }} errors/s on {{ $labels.device }}"
```

---

## Action Priority


| Priority | Items                       | Effort                            |
| -------- | --------------------------- | --------------------------------- |
| Fix now  | #1, #2, #3, #4, #5          | Small — bugs and security         |
| Fix soon | #6, #7, #8, #10, #15        | Medium — correctness and security |
| Plan for | #9, #11, #12, #13, #14, #16 | Medium — reliability              |
| Backlog  | #17–#23                     | Small — polish and best practices |


