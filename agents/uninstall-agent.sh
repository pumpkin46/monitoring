#!/bin/bash  
# ================================================================  
# Grafana Alloy - Uninstaller  
# Run on each target server to completely remove Alloy  
#  
# Usage:  
#   sudo bash uninstall-agent.sh  
# ================================================================  

# ── Check root ────────────────────────────────────────────────────  
if [ "$EUID" -ne 0 ]; then  
  echo "ERROR: Please run as root (sudo bash uninstall-agent.sh)"  
  exit 1  
fi  

echo "========================================================"  
echo " Uninstalling Grafana Alloy from: $(hostname)"  
echo "========================================================"  

# ================================================================  
# STEP 1 — Stop and disable Alloy service  
# ================================================================  
echo ""  
echo "[STEP 1] Stopping Alloy service..."  

if systemctl is-active --quiet alloy; then  
  systemctl stop alloy  
  echo "✓ Alloy service stopped"  
else  
  echo "  Alloy was not running"  
fi  

systemctl disable alloy 2>/dev/null  
echo "✓ Alloy service disabled"  

# ================================================================  
# STEP 2 — Remove Alloy package  
# ================================================================  
echo ""  
echo "[STEP 2] Removing Alloy package..."  

apt-get remove -y alloy  
apt-get autoremove -y  

echo "✓ Alloy package removed"  

# ================================================================  
# STEP 3 — Remove config and data files  
# ================================================================  
echo ""  
echo "[STEP 3] Removing config and data files..."  

# Config  
rm -rf /etc/alloy  
echo "✓ Removed /etc/alloy"  

# Data and positions files (log read positions)  
rm -rf /var/lib/alloy  
echo "✓ Removed /var/lib/alloy"  

# Alloy WAL (write-ahead log for metrics buffer)  
rm -rf /tmp/alloy-wal 2>/dev/null  
echo "✓ Removed WAL data"  

# ================================================================  
# STEP 4 — Remove systemd service file (if leftover)  
# ================================================================  
echo ""  
echo "[STEP 4] Cleaning up systemd..."  

rm -f /etc/systemd/system/alloy.service  
rm -f /etc/systemd/system/alloy.service.d/ 2>/dev/null  
systemctl daemon-reload  
systemctl reset-failed alloy 2>/dev/null  

echo "✓ Systemd cleaned up"  

# ================================================================  
# STEP 5 — Remove Grafana repository  
# ================================================================  
echo ""  
echo "[STEP 5] Removing Grafana apt repository..."  

rm -f /etc/apt/sources.list.d/grafana.list  
rm -f /etc/apt/keyrings/grafana.gpg  
apt-get update -q  

echo "✓ Grafana repository removed"  

# ================================================================  
# STEP 6 — Remove firewall rules (if UFW is active)  
# ================================================================  
echo ""  
echo "[STEP 6] Cleaning up firewall rules..."  

if command -v ufw &>/dev/null && ufw status | grep -q "active"; then  
  # Delete the rules added by install script  
  ufw delete allow out to any port 9090 2>/dev/null  
  ufw delete allow out to any port 3100 2>/dev/null  
  echo "✓ UFW rules removed"  
else  
  echo "  UFW not active — skipping"  
fi  

# ================================================================  
# STEP 7 — Remove alloy system user (if exists)  
# ================================================================  
echo ""  
echo "[STEP 7] Removing alloy user..."  

if id "alloy" &>/dev/null; then  
  userdel alloy 2>/dev/null  
  echo "✓ alloy user removed"  
else  
  echo "  alloy user not found — skipping"  
fi  

# ================================================================  
# VERIFY — Confirm everything is gone  
# ================================================================  
echo ""  
echo "========================================================"  
echo " Verifying uninstall..."  
echo "========================================================"  

ERRORS=0  

# Check service  
if systemctl list-units --all | grep -q "alloy.service"; then  
  echo "⚠ alloy.service still exists"  
  ERRORS=$((ERRORS + 1))  
else  
  echo "✓ Service removed"  
fi  

# Check binary  
if command -v alloy &>/dev/null; then  
  echo "⚠ alloy binary still found at: $(which alloy)"  
  ERRORS=$((ERRORS + 1))  
else  
  echo "✓ Binary removed"  
fi  

# Check config  
if [ -d "/etc/alloy" ]; then  
  echo "⚠ /etc/alloy still exists"  
  ERRORS=$((ERRORS + 1))  
else  
  echo "✓ Config removed"  
fi  

# Check data  
if [ -d "/var/lib/alloy" ]; then  
  echo "⚠ /var/lib/alloy still exists"  
  ERRORS=$((ERRORS + 1))  
else  
  echo "✓ Data removed"  
fi  

echo ""  
if [ "$ERRORS" -eq 0 ]; then  
  echo "========================================================"  
  echo " ✓ Alloy completely removed from: $(hostname)"  
  echo "========================================================"  
else  
  echo "========================================================"  
  echo " ⚠ Uninstall completed with $ERRORS warning(s)"  
  echo "   Review items above and remove manually if needed"  
  echo "========================================================"  
fi  
