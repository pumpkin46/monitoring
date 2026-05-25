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


def build_log_panels(server: str, app_jobs: list[str]) -> list:
    job_pattern = "|".join(app_jobs)
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
            "title": "Log Volume",
            "type": "timeseries",
            "targets": [
                {
                    "expr": f'sum(rate({{host="{server}"}}[5m]))',
                    "legendFormat": "lines/s",
                }
            ],
        },
    ]
    for pid, title, expr in [
        (202, "System Logs (syslog)", f'{{job="system", host="{server}"}}'),
        (203, "Auth Logs (SSH, sudo)", f'{{job="auth", host="{server}"}}'),
        (204, "Kernel Logs (kern.log)", f'{{job="kernel", host="{server}"}}'),
        (205, "Application Logs", f'{{job=~"{job_pattern}", host="{server}"}}'),
        (206, "Systemd Journal (warnings+)", f'{{job="journal", host="{server}"}}'),
    ]:
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


def relayout(panels: list, start_y: int = 0) -> list:
    y = start_y
    for panel in panels:
        h = panel["gridPos"]["h"]
        panel["gridPos"]["y"] = y
        y += h
    return panels


def generate(server: str, cfg: dict) -> dict:
    metrics = load_json(METRICS_SRC)
    features = cfg["features"]

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

    log_panels = build_log_panels(server, cfg["app_jobs"])
    metric_h = sum(p["gridPos"]["h"] for p in cleaned)
    panels = relayout(cleaned) + relayout(log_panels, start_y=metric_h)

    dash = {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "panels": panels,
        "schemaVersion": 39,
        "tags": ["monitoring", "server", server],
        "templating": {"list": []},
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
