#!/usr/bin/env python3
"""Generate one combined Grafana dashboard (metrics + logs) per server."""

import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
METRICS_SRC = ROOT / "overview" / "server-overview.json"
OUT_DIR = ROOT / "servers"

SERVERS = {
    "manager": {
        "title": "Manager",
        "app_jobs": ["manager", "app"],
        "features": {"http", "nodejs"},
        "probe_match": r"146\.190\.166\.199",
    },
    "receiver": {
        "title": "Receiver",
        "app_jobs": ["receiver", "app"],
        "features": {"http", "nodejs", "receiver_integrations"},
        "probe_match": r"ota-receiver",
    },
    "worker": {
        "title": "Worker",
        "app_jobs": ["worker", "app"],
        "features": {"http", "nodejs"},
        "probe_match": r"WORKER",
    },
    "comparison": {
        "title": "Comparison",
        "app_jobs": ["comparison", "app"],
        "features": {"comparison"},
        "probe_match": r"tools\.localota",
    },
    "pms-api": {
        "title": "PMS API",
        "app_jobs": ["pms-api", "app"],
        "features": {"http", "nodejs"},
        "probe_match": r"gha\.localota",
    },
}

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

INTEGRATION_VAR = {
    "allValue": ".+",
    "current": {"selected": True, "text": "All", "value": "$__all"},
    "datasource": {"type": "prometheus", "uid": "prometheus"},
    "definition": (
        'label_values(http_request_duration_seconds_count{server="receiver"}, '
        "integration)"
    ),
    "includeAll": True,
    "label": "Integration",
    "multi": True,
    "name": "integration",
    "options": [],
    "query": {
        "query": (
            'label_values(http_request_duration_seconds_count{server="receiver"}, '
            "integration)"
        ),
        "refId": "StandardVariableQuery",
    },
    "refresh": 2,
    "sort": 1,
    "type": "query",
}

TS_DEFAULTS = {"fillOpacity": 10, "lineWidth": 2}


def with_log_search(selector: str) -> str:
    return f'{selector} |= "${{log_search}}"'


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
        "targets": [{"expr": expr, "legendFormat": legend}],
    }
    return panel


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


def generate(server: str, cfg: dict) -> dict:
    metrics = load_json(METRICS_SRC)
    features = cfg["features"]
    is_receiver = server == "receiver"

    metric_panels = [
        copy.deepcopy(p)
        for p in metrics["panels"]
        if keep_metric_panel(p, features)
    ]
    metric_panels.extend(probe_panels(metrics["panels"], cfg["probe_match"]))

    # Drop duplicate consecutive rows
    cleaned = []
    for p in metric_panels:
        if p.get("type") == "row" and cleaned and cleaned[-1].get("type") == "row":
            if p.get("title") == cleaned[-1].get("title"):
                continue
        cleaned.append(p)

    if is_receiver:
        customize_receiver_panels(cleaned)
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
    return dash


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for server, cfg in SERVERS.items():
        path = OUT_DIR / f"{server}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(generate(server, cfg), f, indent=2)
            f.write("\n")
        print(f"Wrote {path.name}")


if __name__ == "__main__":
    main()
