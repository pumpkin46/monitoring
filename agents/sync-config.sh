#!/bin/bash
# ================================================================
# Grafana Alloy — config-only sync (no package install)
# Used by GitHub Actions CD after rsync of agents/alloy templates.
#
# Usage:
#   sudo bash sync-config.sh <server-name> [monitoring-ip]
# ================================================================

SERVER_NAME=$1
MONITORING_IP=${2:-"74.208.122.144"}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="${SCRIPT_DIR}/alloy"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Run as root (sudo bash sync-config.sh)"
  exit 1
fi

if [ -z "$SERVER_NAME" ]; then
  echo "ERROR: Server name is required"
  echo "Usage: sudo bash sync-config.sh <server-name> [monitoring-ip]"
  exit 1
fi

if [ ! -f "${TEMPLATE_DIR}/config.alloy" ]; then
  echo "ERROR: Template not found at ${TEMPLATE_DIR}/config.alloy"
  exit 1
fi

if ! systemctl list-unit-files alloy.service >/dev/null 2>&1; then
  echo "ERROR: alloy.service not found — run install-agent.sh first"
  exit 1
fi

echo "Syncing Alloy config for: $SERVER_NAME (monitoring: $MONITORING_IP)"

cp "${TEMPLATE_DIR}/config.alloy" /etc/alloy/config.alloy
sed -i "s/SERVER_NAME/${SERVER_NAME}/g" /etc/alloy/config.alloy
sed -i "s/MONITORING_IP/${MONITORING_IP}/g" /etc/alloy/config.alloy

ADDON_FILE="${TEMPLATE_DIR}/config-${SERVER_NAME}.alloy"
if [ -f "$ADDON_FILE" ]; then
  echo "" >> /etc/alloy/config.alloy
  cat "$ADDON_FILE" >> /etc/alloy/config.alloy
  sed -i "s/SERVER_NAME/${SERVER_NAME}/g" /etc/alloy/config.alloy
fi

chown alloy:alloy /etc/alloy/config.alloy 2>/dev/null || true

GRANT_PM2="${SCRIPT_DIR}/grant-pm2-logs.sh"
if [ -f "$GRANT_PM2" ]; then
  bash "$GRANT_PM2" "$SERVER_NAME"
fi

systemctl restart alloy
sleep 2

if systemctl is-active --quiet alloy; then
  echo "Alloy restarted successfully on $SERVER_NAME"
else
  echo "ERROR: Alloy failed to start"
  journalctl -u alloy -n 20 --no-pager
  exit 1
fi
