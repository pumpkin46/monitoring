#!/bin/bash
# ================================================================
# Grafana Alloy - Agent Installer
# Run on each target server (manager, receiver, worker, etc.)
#
# Usage:
#   sudo bash install-agent.sh <server-name>
#
# Examples:
#   sudo bash install-agent.sh manager
#   sudo bash install-agent.sh receiver
#   sudo bash install-agent.sh worker
#   sudo bash install-agent.sh comparison
#   sudo bash install-agent.sh pms-api
# ================================================================

# ── Variables ─────────────────────────────────────────────────────
SERVER_NAME=$1
MONITORING_IP="74.208.122.144"    # CHANGE to your monitoring server IP
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="${SCRIPT_DIR}/alloy"

# ── Check root and argument ───────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Please run as root (sudo bash install-agent.sh)"
  exit 1
fi

if [ -z "$SERVER_NAME" ]; then
  echo "ERROR: Server name is required"
  echo "Usage: sudo bash install-agent.sh <server-name>"
  exit 1
fi

if [ ! -f "${TEMPLATE_DIR}/config.alloy" ]; then
  echo "ERROR: Template file not found at ${TEMPLATE_DIR}/config.alloy"
  echo "Make sure the alloy/ directory is next to this script."
  exit 1
fi

echo "========================================================"
echo " Installing Grafana Alloy on: $SERVER_NAME"
echo " Monitoring server: $MONITORING_IP"
echo "========================================================"

# ================================================================
# STEP 1 — Add Grafana APT repository
# ================================================================
echo ""
echo "[STEP 1] Adding Grafana repository..."

apt-get install -y apt-transport-https software-properties-common wget curl gpg

mkdir -p /etc/apt/keyrings/

wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg

echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | tee /etc/apt/sources.list.d/grafana.list

apt-get update -q

echo "✓ Grafana repository added"

# ================================================================
# STEP 2 — Install Alloy
# ================================================================
echo ""
echo "[STEP 2] Installing Grafana Alloy..."

apt-get install -y alloy

echo "✓ Alloy installed: $(alloy --version)"

# ================================================================
# STEP 3 — Create directories
# ================================================================
echo ""
echo "[STEP 3] Creating directories..."

mkdir -p /etc/alloy
mkdir -p /var/log/app
mkdir -p /var/lib/alloy/data

case "$SERVER_NAME" in
  manager|receiver|worker|comparison|pms-api)
    mkdir -p "/var/log/app/${SERVER_NAME}"
    ;;
esac

echo "✓ Directories created"

# ================================================================
# STEP 4 — Deploy Alloy configuration from template
# ================================================================
echo ""
echo "[STEP 4] Writing Alloy config to /etc/alloy/config.alloy..."

cp "${TEMPLATE_DIR}/config.alloy" /etc/alloy/config.alloy

sed -i "s/SERVER_NAME/${SERVER_NAME}/g" /etc/alloy/config.alloy
sed -i "s/MONITORING_IP/${MONITORING_IP}/g" /etc/alloy/config.alloy

ADDON_FILE="${TEMPLATE_DIR}/config-${SERVER_NAME}.alloy"
if [ -f "$ADDON_FILE" ]; then
  echo "" >> /etc/alloy/config.alloy
  cat "$ADDON_FILE" >> /etc/alloy/config.alloy
  sed -i "s/SERVER_NAME/${SERVER_NAME}/g" /etc/alloy/config.alloy
  echo "  (appended ${SERVER_NAME} addon: config-${SERVER_NAME}.alloy)"
fi

echo "✓ Config written to /etc/alloy/config.alloy"

# ================================================================
# STEP 5 — Set correct permissions
# ================================================================
echo ""
echo "[STEP 5] Setting permissions..."

usermod -aG adm alloy 2>/dev/null || true
chown -R alloy:alloy /var/lib/alloy/

echo "✓ Permissions set"

# ================================================================
# STEP 6 — Enable and start Alloy service
# ================================================================
echo ""
echo "[STEP 6] Starting Alloy service..."

systemctl daemon-reload
systemctl enable alloy
systemctl restart alloy

sleep 3

if systemctl is-active --quiet alloy; then
  echo "✓ Alloy is running"
else
  echo "✗ Alloy failed to start — check logs:"
  journalctl -u alloy -n 20 --no-pager
  exit 1
fi

# ================================================================
# STEP 7 — Firewall rules (if UFW is active)
# ================================================================
echo ""
echo "[STEP 7] Checking firewall..."

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "active"; then
  ufw allow out to "$MONITORING_IP" port 9090 proto tcp comment "Alloy to Prometheus"
  ufw allow out to "$MONITORING_IP" port 3100 proto tcp comment "Alloy to Loki"
  echo "✓ UFW rules added"
else
  echo "  UFW not active — skipping (configure your firewall manually)"
fi

# ================================================================
# STEP 8 — Verify connection to monitoring server
# ================================================================
echo ""
echo "[STEP 8] Testing connection to monitoring server..."

if curl -s --connect-timeout 5 "http://${MONITORING_IP}:9090/-/healthy" | grep -q "Healthy"; then
  echo "✓ Prometheus is reachable"
else
  echo "⚠ Cannot reach Prometheus at ${MONITORING_IP}:9090"
  echo "  Check: firewall rules, monitoring server is running"
fi

if curl -s --connect-timeout 5 "http://${MONITORING_IP}:3100/ready" | grep -q "ready"; then
  echo "✓ Loki is reachable"
else
  echo "⚠ Cannot reach Loki at ${MONITORING_IP}:3100"
  echo "  Check: firewall rules, monitoring server is running"
fi

# ================================================================
# DONE
# ================================================================
echo ""
echo "========================================================"
echo " ✓ Alloy installed successfully on: $SERVER_NAME"
echo "========================================================"
echo ""
echo "Useful commands:"
echo "  Status  : systemctl status alloy"
echo "  Logs    : journalctl -u alloy -f"
echo "  Restart : systemctl restart alloy"
echo "  Config  : /etc/alloy/config.alloy"
echo "  Web UI  : http://$(hostname -I | awk '{print $1}'):12345"
echo ""
echo "After ~30 seconds, check Prometheus:"
echo "  http://${MONITORING_IP}:9090/targets"
echo "  Query: node_uname_info{server=\"${SERVER_NAME}\"}"
