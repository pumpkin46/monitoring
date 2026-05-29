#!/bin/sh
# Create nested Servers/* folders, then reload dashboards (after Grafana API is up).
set -e

/run.sh &
grafana_pid=$!

export GRAFANA_URL=http://127.0.0.1:3000
export GRAFANA_SKIP_WAIT=0

echo "Starting Grafana (pid ${grafana_pid})..."

i=0
while [ "$i" -lt 120 ]; do
  if wget -q -O- "${GRAFANA_URL}/api/health" >/dev/null 2>&1; then
    echo "Grafana API is ready."
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ "$i" -ge 120 ]; then
  echo "Grafana API did not become ready." >&2
  kill "$grafana_pid" 2>/dev/null || true
  exit 1
fi

export GRAFANA_SKIP_WAIT=1
/bin/sh /etc/grafana/provisioning/init-folders.sh

wait "$grafana_pid"
