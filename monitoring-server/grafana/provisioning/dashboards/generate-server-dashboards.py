#!/usr/bin/env python3
"""Generate Grafana dashboards: overview app logs + per-server metrics/logs."""

import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
METRICS_SRC = ROOT / "overview" / "server-overview.json"
SERVERS_ROOT = ROOT / "servers"
OUT_DIR = SERVERS_ROOT  # servers/<server>/*.json on disk

# Servers whose dashboards are edited manually — never overwritten by main()
HAND_MAINTAINED = frozenset({"receiver"})

# probe_url must match monitoring-server/prometheus/prometheus.yml http-probes targets
SERVERS = {
    "manager": {
        "title": "Manager",
        "app_jobs": ["manager", "app"],
        "features": {"http", "nodejs"},
        "probe_url": "http://146.190.166.199:9000/",
    },
    "receiver": {
        "title": "Receiver",
        "app_jobs": ["receiver", "app"],
        "features": {"http", "nodejs", "receiver_integrations"},
        "probe_url": "https://ota-receiver.localota.stream/healthz",
    },
    "worker": {
        "title": "Worker",
        "app_jobs": ["worker", "app"],
        "features": {"http", "nodejs"},
        "probe_url": "http://147.182.204.142:8001/",
    },
    "comparison": {
        "title": "Comparison",
        "app_jobs": ["comparison", "app"],
        "features": {"comparison"},
        "probe_url": "https://tools.localota.stream/health",
    },
    "pms-api": {
        "title": "PMS API",
        "app_jobs": ["pms-api", "app"],
        "features": {"http", "nodejs"},
        "probe_url": "https://gha.localota.stream",
    },
    "scraper": {
        "title": "Scraper",
        "app_jobs": ["scraper", "app"],
        "features": {"http", "nodejs"},
        "probe_url": "https://scrape.localota.com/",
    },
}


def probe_match_from_url(url: str) -> str:
    """PromQL-safe regex for probe_success{instance=~\"...\"} from Blackbox target URL."""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    return re.sub(r"\.", r"[.]", host)

PANEL_FEATURES = {
    15: "comparison",
    16: "comparison",
    17: "http",
    18: "http",
    19: "http",
    20: "receiver_integrations",
    21: "nodejs",
}

LOG_OPTS = {
    "showTime": True,
    "showLabels": True,
    "showCommonLabels": False,
    "wrapLogMessage": True,
    "prettifyLogMessage": False,
    "enableLogDetails": True,
    "sortOrder": "Descending",
    "dedupStrategy": "none",
}

LOG_SEARCH_VAR = {
    "current": {"selected": False, "text": "", "value": ""},
    "description": "Filter log lines containing this text. Leave empty to show all.",
    "label": "Search logs",
    "name": "log_search",
    "options": [{"selected": True, "text": "", "value": ""}],
    "query": "",
    "type": "textbox",
}

# Alloy scrapes GET /metrics every 15s (~4 req/min per container). Health and
# realtime polling are ops traffic, not channel API load.
RECEIVER_HTTP_EXCLUDE = (
    'path!="/metrics", path!="/readyz", path!~"/healthz?", '
    'path!~"/healthcheck?"'
)

INTEGRATION_VAR = {
    "allValue": ".+",
    "current": {"selected": True, "text": "All", "value": "$__all"},
    "datasource": {"type": "prometheus", "uid": "prometheus"},
    "definition": (
        "label_values(http_request_duration_seconds_count"
        '{server="receiver", '
        f"{RECEIVER_HTTP_EXCLUDE}}}, integration)"
    ),
    "includeAll": True,
    "label": "Integration",
    "multi": True,
    "name": "integration",
    "options": [],
        "query": {
            "query": (
                "label_values(http_request_duration_seconds_count"
                '{server="receiver", '
                f"{RECEIVER_HTTP_EXCLUDE}}}, integration)"
            ),
        "refId": "StandardVariableQuery",
    },
    "refresh": 2,
    "sort": 1,
    "type": "query",
}

TS_DEFAULTS = {"fillOpacity": 10, "lineWidth": 2}

# Legacy Receiver Dashboard channels (path regex → integration label)
RECEIVER_CHANNELS = [
    ("myallocator", "Myallocator"),
    ("hyperguest", "Hyperguest"),
    ("ownerrez", "OwnerRez"),
    ("channex", "Channex"),
    ("ratedock", "RateDock"),
    ("localota", "Localota"),
]
RECEIVER_CHANNEL_MATCH = "|".join(name for name, _ in RECEIVER_CHANNELS)


def with_log_search(selector: str) -> str:
    return f'{selector} |= "${{log_search}}"'


def app_error_selector(
    server: str | None,
    app_jobs: list[str],
    *,
    receiver: bool = False,
    all_servers: bool = False,
) -> str:
    """PM2 *-error.log (filename) and Docker stderr (stream)."""
    job_pattern = "|".join(app_jobs)
    if all_servers:
        host = 'host=~"$server"'
    else:
        host = f'host="{server}"'
    extra = ', integration=~"$integration"' if receiver else ""
    return (
        f'{{job=~"{job_pattern}", {host}{extra}}} '
        f'| filename=~".*-error\\\\.log" or stream="stderr"'
    )


def all_app_jobs() -> str:
    jobs: set[str] = set()
    for cfg in SERVERS.values():
        jobs.update(cfg["app_jobs"])
    return "|".join(sorted(jobs))


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fix_queries(obj, server: str, app_jobs: list[str]) -> None:
    job_pattern = "|".join(re.escape(j) for j in app_jobs)

    def walk(item):
        if isinstance(item, dict):
            for key, val in list(item.items()):
                if key == "expr" and isinstance(val, str):
                    val = val.replace('server=~"$server"', f'server="{server}"')
                    val = val.replace('host=~"$server"', f'host="{server}"')
                    val = val.replace('server="receiver"', f'server="{server}"')
                    val = re.sub(
                        r'\{job=~"[^"]+", host=~"\$server"\}',
                        f'{{job=~"{job_pattern}", host="{server}"}}',
                        val,
                    )
                    val = re.sub(
                        r'\(1 - rate\(node_cpu_seconds_total\{mode="idle", server="([^"]+)"\}\[5m\]\)\) \* 100',
                        r'(1 - avg(rate(node_cpu_seconds_total{mode="idle", server="\1"}[5m]))) * 100',
                        val,
                    )
                    val = re.sub(
                        r"sum by \(server, le\)",
                        "sum by (le)",
                        val,
                    )
                    val = re.sub(
                        r"sum by \(server, method, path\)",
                        "sum by (method, path)",
                        val,
                    )
                    val = re.sub(r"sum by \(server\) \(", "sum(", val)
                    val = val.replace("{{ server }} — ", "")
                    val = val.replace("{{ server }} ", "")
                    val = val.replace("{{ server }}", server)
                    item[key] = val
                else:
                    walk(val)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(obj)


def inject_receiver_http_excludes(obj: dict) -> None:
    """Drop scrape/health/realtime paths from receiver HTTP metric queries."""

    def walk(item):
        if isinstance(item, dict):
            for key, val in list(item.items()):
                if key == "expr" and isinstance(val, str):
                    if (
                        "http_request_duration_seconds" in val
                        and 'server="receiver"' in val
                        and 'path!="/metrics"' not in val
                    ):
                        item[key] = val.replace(
                            '{server="receiver"',
                            f'{{server="receiver", {RECEIVER_HTTP_EXCLUDE}',
                            1,
                        )
                else:
                    walk(val)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(obj)


def keep_metric_panel(panel: dict, features: set[str]) -> bool:
    if panel.get("type") == "row":
        if panel.get("title") == "Application Metrics":
            return bool(
                features & {"comparison", "http", "nodejs", "receiver_integrations"}
            )
        return panel.get("title") != "HTTP Probes (Blackbox)"
    pid = panel.get("id")
    if pid in PANEL_FEATURES:
        return PANEL_FEATURES[pid] in features
    return "probe_" not in json.dumps(panel.get("targets", []))


def probe_panels(panels: list, probe_match: str) -> list:
    out = []
    in_section = False
    for panel in panels:
        if panel.get("title") == "HTTP Probes (Blackbox)":
            in_section = True
            out.append(copy.deepcopy(panel))
            continue
        if in_section:
            if panel.get("type") == "row":
                break
            p = copy.deepcopy(panel)
            for t in p.get("targets", []):
                if "probe_" in t.get("expr", ""):
                    t["expr"] = f'{t["expr"]}{{instance=~"{probe_match}"}}'
            out.append(p)
    return out


def build_log_panels(server: str, app_jobs: list[str], *, receiver: bool = False) -> list:
    job_pattern = "|".join(app_jobs)
    if receiver:
        volume_expr = (
            f'sum by (integration) (rate({{job="receiver", host="{server}"}} '
            f'|= "${{log_search}}" [5m]))'
        )
        volume_legend = "{{ integration }}"
    else:
        volume_expr = (
            f'sum(rate({{host="{server}"}} |= "${{log_search}}" [5m]))'
        )
        volume_legend = "lines/s"

    panels = [
        {
            "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
            "id": 200,
            "title": "Logs",
            "type": "row",
        },
        {
            "datasource": {"type": "loki", "uid": "loki"},
            "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
            "id": 201,
            "fieldConfig": {
                "defaults": {
                    "unit": "short",
                    "custom": {"fillOpacity": 30, "lineWidth": 1},
                }
            },
            "options": {"tooltip": {"mode": "multi"}},
            "title": "Log Volume" + (" by Integration" if receiver else ""),
            "type": "timeseries",
            "targets": [
                {
                    "expr": volume_expr,
                    "legendFormat": volume_legend,
                }
            ],
        },
    ]
    log_streams = [
        (202, "System Logs (syslog)", f'{{job="system", host="{server}"}}'),
        (203, "Auth Logs (SSH, sudo)", f'{{job="auth", host="{server}"}}'),
        (204, "Kernel Logs (kern.log)", f'{{job="kernel", host="{server}"}}'),
        (
            205,
            "Application Logs",
            f'{{job=~"{job_pattern}", host="{server}"'
            + (', integration=~"$integration"' if receiver else "")
            + "}",
        ),
        (
            207,
            "Application Error Logs",
            app_error_selector(
                server, app_jobs, receiver=receiver
            ),
        ),
        (206, "Systemd Journal (warnings+)", f'{{job="journal", host="{server}"}}'),
    ]
    for pid, title, selector in log_streams:
        expr = with_log_search(selector)
        panels.append(
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "gridPos": {"h": 8, "w": 24, "x": 0, "y": 0},
                "id": pid,
                "options": LOG_OPTS,
                "title": title,
                "type": "logs",
                "targets": [{"expr": expr, "refId": "A"}],
            }
        )
    return panels


def build_app_error_logs_overview() -> dict:
    """All-servers application error log dashboard (overview/app-error-logs-overview.json)."""
    job_pattern = all_app_jobs()
    app_jobs = job_pattern.split("|")
    error_selector = app_error_selector(None, app_jobs, all_servers=True)
    error_volume_expr = (
        f'sum by (host) (rate({error_selector} |= "${{log_search}}" [5m]))'
    )
    server_query = f'label_values({{job=~"{job_pattern}"}}, host)'
    panels = reflow_panels(
        [
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 100,
                "title": "Application Error Logs",
                "type": "row",
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {
                            "fillOpacity": 30,
                            "lineWidth": 1,
                            "stacking": {"mode": "normal"},
                        },
                    }
                },
                "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
                "id": 1,
                "options": {"tooltip": {"mode": "multi"}},
                "title": "Error Log Lines per Server",
                "type": "timeseries",
                "targets": [
                    {
                        "expr": error_volume_expr,
                        "legendFormat": "{{ host }}",
                    }
                ],
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "gridPos": {"h": 18, "w": 24, "x": 0, "y": 0},
                "id": 2,
                "options": LOG_OPTS,
                "title": "Application Error Logs",
                "type": "logs",
                "targets": [
                    {
                        "expr": with_log_search(error_selector),
                        "refId": "A",
                    }
                ],
            },
        ]
    )
    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "panels": panels,
        "schemaVersion": 39,
        "tags": ["monitoring", "logs", "loki", "application", "errors"],
        "templating": {
            "list": [
                LOG_SEARCH_VAR,
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "loki", "uid": "loki"},
                    "definition": server_query,
                    "includeAll": True,
                    "label": "Server",
                    "multi": True,
                    "name": "server",
                    "options": [],
                    "query": server_query,
                    "refresh": 2,
                    "type": "query",
                },
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "title": "All Servers — Application Error Logs",
        "uid": "app-error-logs-overview",
    }


def build_app_logs_overview() -> dict:
    """All-servers application log dashboard (overview/app-logs-overview.json)."""
    job_pattern = all_app_jobs()
    app_jobs = job_pattern.split("|")
    app_selector = f'{{job=~"{job_pattern}", host=~"$server"}}'
    error_selector = app_error_selector(
        None, app_jobs, all_servers=True
    )
    volume_expr = (
        f'sum by (host) (rate({app_selector} |= "${{log_search}}" [5m]))'
    )
    error_volume_expr = (
        f'sum by (host) (rate({error_selector} |= "${{log_search}}" [5m]))'
    )
    server_query = f'label_values({{job=~"{job_pattern}"}}, host)'
    panels = reflow_panels(
        [
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 100,
                "title": "Application Logs",
                "type": "row",
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {
                            "fillOpacity": 30,
                            "lineWidth": 1,
                            "stacking": {"mode": "normal"},
                        },
                    }
                },
                "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
                "id": 1,
                "options": {"tooltip": {"mode": "multi"}},
                "title": "Log Lines per Server",
                "type": "timeseries",
                "targets": [
                    {
                        "expr": volume_expr,
                        "legendFormat": "{{ host }}",
                    }
                ],
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "gridPos": {"h": 14, "w": 24, "x": 0, "y": 0},
                "id": 2,
                "options": LOG_OPTS,
                "title": "Application Logs",
                "type": "logs",
                "targets": [
                    {
                        "expr": with_log_search(app_selector),
                        "refId": "A",
                    }
                ],
            },
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                "id": 101,
                "title": "Application Error Logs",
                "type": "row",
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {
                            "fillOpacity": 30,
                            "lineWidth": 1,
                            "stacking": {"mode": "normal"},
                        },
                    }
                },
                "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
                "id": 3,
                "options": {"tooltip": {"mode": "multi"}},
                "title": "Error Log Lines per Server",
                "type": "timeseries",
                "targets": [
                    {
                        "expr": error_volume_expr,
                        "legendFormat": "{{ host }}",
                    }
                ],
            },
            {
                "datasource": {"type": "loki", "uid": "loki"},
                "gridPos": {"h": 14, "w": 24, "x": 0, "y": 0},
                "id": 4,
                "options": LOG_OPTS,
                "title": "Application Error Logs",
                "type": "logs",
                "targets": [
                    {
                        "expr": with_log_search(error_selector),
                        "refId": "A",
                    }
                ],
            },
        ]
    )
    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "panels": panels,
        "schemaVersion": 39,
        "tags": ["monitoring", "logs", "loki", "application"],
        "templating": {
            "list": [
                LOG_SEARCH_VAR,
                {
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "datasource": {"type": "loki", "uid": "loki"},
                    "definition": server_query,
                    "includeAll": True,
                    "label": "Server",
                    "multi": True,
                    "name": "server",
                    "options": [],
                    "query": server_query,
                    "refresh": 2,
                    "type": "query",
                },
            ]
        },
        "time": {"from": "now-1h", "to": "now"},
        "title": "All Servers — Application Logs",
        "uid": "app-logs-overview",
    }


def reflow_panels(panels: list, start_y: int = 0) -> list:
    """Pack panels left-to-right in a 24-column grid, wrapping at row boundaries."""
    y = start_y
    x = 0
    row_h = 0

    for panel in panels:
        gp = panel["gridPos"]
        w = gp["w"]
        h = gp["h"]

        if panel.get("type") == "row":
            if row_h:
                y += row_h
            gp["x"] = 0
            gp["y"] = y
            y += h
            x = 0
            row_h = 0
            continue

        if x > 0 and x + w > 24:
            y += row_h
            x = 0
            row_h = 0

        gp["x"] = x
        gp["y"] = y
        row_h = max(row_h, h)
        x += w

    return panels


def prom_ts_panel(
    pid: int,
    title: str,
    expr: str,
    *,
    legend: str = "",
    unit: str = "short",
    w: int = 12,
    h: int = 8,
    stacking: str | None = None,
    targets: list[dict] | None = None,
) -> dict:
    custom = dict(TS_DEFAULTS)
    if stacking:
        custom["stacking"] = {"mode": stacking}
    panel = {
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "fieldConfig": {"defaults": {"unit": unit, "custom": custom}},
        "gridPos": {"h": h, "w": w, "x": 0, "y": 0},
        "id": pid,
        "options": {"tooltip": {"mode": "multi"}},
        "title": title,
        "type": "timeseries",
        "targets": targets
        if targets is not None
        else [{"expr": expr, "legendFormat": legend}],
    }
    return panel


def prom_stat_panel(pid: int, title: str, expr: str, *, w: int = 5, h: int = 5) -> dict:
    return {
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "fieldConfig": {
            "defaults": {
                "mappings": [
                    {
                        "options": {"match": "null", "result": {"text": "N/A"}},
                        "type": "special",
                    }
                ],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "green", "value": None}],
                },
                "unit": "none",
            }
        },
        "gridPos": {"h": h, "w": w, "x": 0, "y": 0},
        "id": pid,
        "options": {
            "colorMode": "none",
            "graphMode": "none",
            "justifyMode": "center",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value",
        },
        "title": title,
        "type": "stat",
        "targets": [{"expr": expr, "legendFormat": ""}],
    }


def prom_bargauge_panel(
    pid: int, title: str, targets: list[dict], *, w: int = 12, h: int = 5
) -> dict:
    return {
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "fieldConfig": {
            "defaults": {
                "decimals": 0,
                "min": 0,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "rgba(50, 172, 45, 0.97)", "value": None},
                        {"color": "rgba(237, 129, 40, 0.89)", "value": 10},
                        {"color": "rgba(245, 54, 54, 0.9)", "value": 40},
                    ],
                },
                "unit": "none",
            }
        },
        "gridPos": {"h": h, "w": w, "x": 0, "y": 0},
        "id": pid,
        "options": {
            "displayMode": "basic",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True,
            "valueMode": "text",
        },
        "title": title,
        "type": "bargauge",
        "targets": targets,
    }


def receiver_legacy_panels() -> list:
    """Migrated Receiver Dashboard (integration label, server=receiver)."""
    base = f'server="receiver", {RECEIVER_HTTP_EXCLUDE}'
    ch = f'integration=~"{RECEIVER_CHANNEL_MATCH}"'

    bargauge_targets = [
        {
            "expr": (
                "sum(increase(http_request_duration_seconds_count"
                f'{{{base}, integration="{name}"}}[$__range]))'
            ),
            "legendFormat": label,
        }
        for name, label in RECEIVER_CHANNELS
    ]

    global_rate_targets = [
        {
            "expr": (
                "sum(rate(http_request_duration_seconds_count"
                f'{{{base}, {ch}}}[1m]))'
            ),
            "legendFormat": "Total",
        }
    ]
    global_rate_targets.extend(
        {
            "expr": (
                "sum(rate(http_request_duration_seconds_count"
                f'{{{base}, integration="{name}"}}[1m]))'
            ),
            "legendFormat": label,
        }
        for name, label in RECEIVER_CHANNELS
    )

    duration_buckets = [
        ("+Inf", "Duration < 1ms (%)"),
        ("0.003", "Duration < 3ms (%)"),
        ("0.03", "Duration < 30ms (%)"),
        ("0.1", "Duration < 100ms (%)"),
        ("0.3", "Duration < 300ms (%)"),
        ("1.5", "Duration < 1500ms (%)"),
        ("10", "Duration < 10000ms (%)"),
    ]
    duration_targets = [
        {
            "expr": (
                f"(sum(irate(http_request_duration_seconds_bucket"
                f'{{{base}, le="{le}"}}[1m])) / '
                f"sum(irate(http_request_duration_seconds_bucket{{{base}}}[1m]))) * 100"
            ),
            "legendFormat": legend,
        }
        for le, legend in duration_buckets
    ]

    panels: list[dict] = [
        {
            "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
            "id": 299,
            "title": "Receiver Channels",
            "type": "row",
        },
        prom_stat_panel(
            300,
            "Total Requests Count",
            f"sum(increase(http_request_duration_seconds_count{{{base}}}[24h]))",
            w=6,
        ),
        prom_bargauge_panel(
            301,
            "Total Request Count by Channel",
            bargauge_targets,
            w=18,
        ),
        prom_ts_panel(
            302,
            "Global Request Rate by Channel (per Minute)",
            "",
            unit="reqps",
            w=12,
            h=9,
            targets=global_rate_targets,
        ),
        prom_ts_panel(
            303,
            "Top 10 API calls (by path) In Last 24 hours",
            (
                "topk(10, sum by (integration, path) "
                "(increase(http_request_duration_seconds_count"
                f'{{{base}}}[24h])))'
            ),
            legend="{{ integration }} {{ path }}",
            w=12,
            h=9,
        ),
    ]

    pid = 310
    for name, label in RECEIVER_CHANNELS:
        integ = f'integration="{name}"'
        panels.append(
            prom_ts_panel(
                pid,
                f"{label} Request and Error Rates (per Minute)",
                "",
                unit="reqps",
                w=12,
                h=9,
                targets=[
                    {
                        "expr": (
                            "sum(rate(http_request_duration_seconds_count"
                            f'{{{base}, {integ}}}[1m]))'
                        ),
                        "legendFormat": "Request",
                    },
                    {
                        "expr": (
                            "sum(rate(http_request_duration_seconds_count"
                            f'{{{base}, {integ}, status_code=~"2.."}}[1m]))'
                        ),
                        "legendFormat": "2XX Success",
                    },
                    {
                        "expr": (
                            "sum(rate(http_request_duration_seconds_count"
                            f'{{{base}, {integ}, status_code=~"3.."}}[1m]))'
                        ),
                        "legendFormat": "3XX Success",
                    },
                    {
                        "expr": (
                            "sum(rate(http_request_duration_seconds_count"
                            f'{{{base}, {integ}, status_code=~"4.."}}[1m]))'
                        ),
                        "legendFormat": "4XX Errors",
                    },
                    {
                        "expr": (
                            "sum(rate(http_request_duration_seconds_count"
                            f'{{{base}, {integ}, status_code=~"5.."}}[1m]))'
                        ),
                        "legendFormat": "5XX Errors",
                    },
                ],
            )
        )
        pid += 1

    panels.append(
        prom_ts_panel(
            320,
            "Request Duration (%)",
            "",
            unit="percent",
            w=12,
            h=9,
            targets=duration_targets,
        )
    )
    return panels


def receiver_extra_panels() -> list:
    integ = 'integration=~"$integration"'
    return [
        prom_ts_panel(
            22,
            "Integration Scrape Status",
            f"max by (integration) (up{{server=\"receiver\", {integ}}})",
            legend="{{ integration }}",
            unit="short",
            w=12,
        ),
        prom_ts_panel(
            23,
            "HTTP 5xx Rate by Integration",
            (
                "sum by (integration) "
                "(rate(http_request_duration_seconds_count"
                '{server="receiver", status_code=~"5..", '
                f"{integ}}}[5m]))"
            ),
            legend="{{ integration }}",
            unit="reqps",
            w=12,
            stacking="normal",
        ),
    ]


def customize_receiver_panels(panels: list) -> None:
    integ = 'integration=~"$integration"'
    for panel in panels:
        pid = panel.get("id")
        if pid == 17:
            panel["title"] = "HTTP Request Rate by Route"
            panel["targets"] = [
                {
                    "expr": (
                        "sum by (integration, method, path) "
                        "(rate(http_request_duration_seconds_count"
                        f'{{server="receiver", {integ}}}[5m]))'
                    ),
                    "legendFormat": "{{ integration }} {{ method }} {{ path }}",
                }
            ]
        elif pid == 18:
            panel["title"] = "HTTP Latency p95 by Integration"
            panel["targets"] = [
                {
                    "expr": (
                        "histogram_quantile(0.95, sum by (integration, le) "
                        "(rate(http_request_duration_seconds_bucket"
                        f'{{server="receiver", {integ}}}[5m])))'
                    ),
                    "legendFormat": "{{ integration }}",
                }
            ]
        elif pid == 19:
            panel["title"] = "HTTP Error Rate by Integration"
            panel["targets"] = [
                {
                    "expr": (
                        "sum by (integration) "
                        "(rate(http_request_duration_seconds_count"
                        f'{{server="receiver", status_code=~"4..", {integ}}}[5m]))'
                    ),
                    "legendFormat": "{{ integration }} 4xx",
                },
                {
                    "expr": (
                        "sum by (integration) "
                        "(rate(http_request_duration_seconds_count"
                        f'{{server="receiver", status_code=~"5..", {integ}}}[5m]))'
                    ),
                    "legendFormat": "{{ integration }} 5xx",
                },
            ]
        elif pid == 20:
            panel["targets"] = [
                {
                    "expr": (
                        "sum by (integration) "
                        "(rate(http_request_duration_seconds_count"
                        f'{{server="receiver", {integ}}}[5m]))'
                    ),
                    "legendFormat": "{{ integration }}",
                }
            ]
        elif pid == 21:
            panel["title"] = "Node.js Memory by Integration"
            panel["targets"] = [
                {
                    "expr": (
                        f'nodejs_heap_size_used_bytes{{server="receiver", {integ}}}'
                    ),
                    "legendFormat": "{{ integration }} heap",
                },
                {
                    "expr": (
                        f'process_resident_memory_bytes{{server="receiver", {integ}}}'
                    ),
                    "legendFormat": "{{ integration }} RSS",
                },
            ]


def insert_after_panel(panels: list, after_id: int, new_panels: list) -> list:
    out = []
    for panel in panels:
        out.append(panel)
        if panel.get("id") == after_id:
            out.extend(new_panels)
    return out


def split_metrics_and_logs(panels: list) -> tuple[list, list]:
    """Split combined panels at the Logs row (id 200)."""
    split_at = 0
    for i, panel in enumerate(panels):
        if panel.get("type") == "row" and panel.get("title") == "Logs":
            split_at = i
            break
    return reflow_panels(panels[:split_at]), reflow_panels(panels[split_at:])


def generate_split(server: str, cfg: dict) -> tuple[dict, dict]:
    """servers/<name>/metrics.json and logs.json (Grafana folder Servers/<name>/)."""
    combined = generate(server, cfg)
    metric_panels, log_panels = split_metrics_and_logs(combined["panels"])

    metrics_templating: list = []
    if server == "receiver":
        metrics_templating = [INTEGRATION_VAR]

    base = {
        k: v
        for k, v in combined.items()
        if k not in ("panels", "title", "uid", "templating")
    }
    metrics = {
        **base,
        "panels": metric_panels,
        "templating": {"list": metrics_templating},
        "title": f"{cfg['title']} — Metrics",
        "uid": f"{server}-metrics",
    }
    if server == "receiver":
        metrics["refresh"] = "10s"
        metrics["description"] = "Receiver metrics (migrated channel dashboards + host metrics)"
    logs = {
        **base,
        "panels": log_panels,
        "templating": combined["templating"],
        "title": f"{cfg['title']} — Logs",
        "uid": f"{server}-logs",
    }
    return metrics, logs


def generate(server: str, cfg: dict) -> dict:
    metrics = load_json(METRICS_SRC)
    features = cfg["features"]
    is_receiver = server == "receiver"

    metric_panels = [
        copy.deepcopy(p)
        for p in metrics["panels"]
        if keep_metric_panel(p, features)
    ]
    metric_panels.extend(
        probe_panels(metrics["panels"], probe_match_from_url(cfg["probe_url"]))
    )

    # Drop duplicate consecutive rows
    cleaned = []
    for p in metric_panels:
        if p.get("type") == "row" and cleaned and cleaned[-1].get("type") == "row":
            if p.get("title") == cleaned[-1].get("title"):
                continue
        cleaned.append(p)

    if is_receiver:
        customize_receiver_panels(cleaned)
        cleaned = insert_after_panel(cleaned, 106, receiver_legacy_panels())
        cleaned = insert_after_panel(cleaned, 21, receiver_extra_panels())

    log_panels = build_log_panels(
        server, cfg["app_jobs"], receiver=is_receiver
    )
    panels = reflow_panels(cleaned + log_panels)

    templating = [LOG_SEARCH_VAR]
    if is_receiver:
        templating.insert(0, INTEGRATION_VAR)

    dash = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "panels": panels,
        "schemaVersion": 39,
        "tags": ["monitoring", "server", server],
        "templating": {"list": templating},
        "time": {"from": "now-6h", "to": "now"},
        "title": cfg["title"],
        "uid": f"server-{server}",
    }
    fix_queries(dash, server, cfg["app_jobs"])
    if is_receiver:
        inject_receiver_http_excludes(dash)
    return dash


def folder_uid(server: str) -> str:
    return "servers-" + server.replace("_", "-")


def grafana_folder(server: str, cfg: dict) -> str:
    """Nested Grafana folder: Servers/<display title>."""
    return f"Servers/{cfg['title']}"


def migrate_servers_layout() -> None:
    """Normalize on-disk layout to servers/<server>/*.json (flatten legacy paths)."""
    known = set(SERVERS) | HAND_MAINTAINED
    legacy_root = SERVERS_ROOT / "Servers"
    sources = list(SERVERS_ROOT.iterdir())
    if legacy_root.is_dir():
        sources.append(legacy_root)
    for item in sources:
        if not item.is_dir():
            continue
        if item == legacy_root:
            children = list(item.iterdir())
        elif item.name == "Servers" or item.name not in known:
            continue
        else:
            children = [item]
        for child in children:
            if not child.is_dir() or child.name not in known:
                continue
            dest = OUT_DIR / child.name
            dest.mkdir(parents=True, exist_ok=True)
            for path in child.glob("*.json"):
                target = dest / path.name
                if path.resolve() != target.resolve():
                    path.replace(target)
            for sub in child.iterdir():
                if sub.is_dir():
                    for path in sub.glob("*.json"):
                        target = dest / path.name
                        if not target.exists():
                            path.replace(target)
                    sub.rmdir()
            if child != dest and child.exists() and not any(child.iterdir()):
                child.rmdir()
            if child != dest:
                print(f"Migrated -> servers/{child.name}/")
    if legacy_root.is_dir() and not any(legacy_root.iterdir()):
        legacy_root.rmdir()


def write_dashboards_yml() -> None:
    """Emit dashboards.yml: one file provider per server (nested folder via folder:)."""
    path = ROOT / "dashboards.yml"
    lines = [
        "apiVersion: 1",
        "",
        "providers:",
        "  - name: overview",
        "    orgId: 1",
        "    folder: Overview",
        "    type: file",
        "    disableDeletion: true",
        "    editable: true",
        "    options:",
        "      path: /etc/grafana/provisioning/dashboards/overview",
        "",
    ]
    for server in sorted(SERVERS):
        cfg = SERVERS[server]
        lines.extend(
            [
                f"  - name: server-{server}",
                "    orgId: 1",
                f"    folder: {grafana_folder(server, cfg)}",
                f"    folderUid: {folder_uid(server)}",
                "    type: file",
                "    disableDeletion: false",
                "    editable: true",
                "    updateIntervalSeconds: 30",
                "    options:",
                f"      path: /etc/grafana/provisioning/dashboards/servers/{server}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote dashboards.yml")


def main() -> None:
    migrate_servers_layout()
    legacy_flat = list(SERVERS_ROOT.glob("*.json"))

    overview_dir = ROOT / "overview"
    overview_dir.mkdir(exist_ok=True)
    app_logs_path = overview_dir / "app-logs-overview.json"
    with app_logs_path.open("w", encoding="utf-8") as f:
        json.dump(build_app_logs_overview(), f, indent=2)
        f.write("\n")
    print("Wrote overview/app-logs-overview.json")

    app_error_logs_path = overview_dir / "app-error-logs-overview.json"
    with app_error_logs_path.open("w", encoding="utf-8") as f:
        json.dump(build_app_error_logs_overview(), f, indent=2)
        f.write("\n")
    print("Wrote overview/app-error-logs-overview.json")

    for server, cfg in SERVERS.items():
        server_dir = OUT_DIR / server
        server_dir.mkdir(exist_ok=True)
        if server in HAND_MAINTAINED:
            print(f"Skipped servers/{server}/ (hand-maintained)")
            continue
        metrics, logs = generate_split(server, cfg)
        for name, dash in (("metrics", metrics), ("logs", logs)):
            nested = server_dir / name
            if nested.is_dir():
                for child in nested.glob("*.json"):
                    child.unlink()
                nested.rmdir()
            path = server_dir / f"{name}.json"
            with path.open("w", encoding="utf-8") as f:
                json.dump(dash, f, indent=2)
                f.write("\n")
            print(f"Wrote servers/{server}/{path.name}")

    for path in legacy_flat:
        path.unlink()
        print(f"Removed servers/{path.name}")

    legacy_comparison_dir = ROOT / "comparison"
    if legacy_comparison_dir.is_dir():
        for path in legacy_comparison_dir.glob("*.json"):
            path.unlink()
            print(f"Removed comparison/{path.name}")
        legacy_comparison_dir.rmdir()

    write_dashboards_yml()


if __name__ == "__main__":
    main()
