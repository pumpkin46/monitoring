#!/bin/bash
# Grant the alloy user access needed for log collection on this host:
#   - PM2 log files (including under /root)
#   - Docker socket (when Docker is installed, for discovery.docker / loki.source.docker)
# Then verify every log path Alloy is configured to tail on this host.
#
# Usage:
#   sudo bash grant-alloy-access.sh [server-name]
#
# server-name (optional): manager, worker, comparison, pms-api, receiver
#   When set, also checks that this host's expected PM2 log files exist.
#
# /root is normally mode 700, so chmod on log files alone is not enough — alloy
# must be allowed to traverse /root → .pm2 → logs (ACL or 711 on parents).

set -euo pipefail

SERVER_NAME="${1:-}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Run as root (sudo bash grant-alloy-access.sh [server-name])"
  exit 1
fi

if ! id alloy &>/dev/null; then
  echo "ERROR: alloy user not found — install Alloy first"
  exit 1
fi

FAILED=0

grant_docker_access() {
  if ! getent group docker >/dev/null; then
    echo "  — docker group not found (Docker not installed — skipping)"
    return 0
  fi

  if id -nG alloy 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    echo "✓ alloy is already in the docker group"
  else
    usermod -aG docker alloy
    echo "✓ added alloy to the docker group"
  fi
}

verify_docker_socket() {
  [ -S /var/run/docker.sock ] || return 0

  echo ""
  echo "Verifying Docker socket access for alloy..."

  if sudo -u alloy sg docker -c 'curl -sf --unix-socket /var/run/docker.sock http://localhost/containers/json?limit=1 >/dev/null'; then
    echo "✓ alloy can list Docker containers"
  else
    echo "✗ alloy cannot access /var/run/docker.sock (run: systemctl restart alloy)"
    FAILED=1
  fi
}

grant_root_pm2_logs() {
  local logs_dir="/root/.pm2/logs"
  [ -d "$logs_dir" ] || return 0

  if command -v setfacl &>/dev/null; then
    setfacl -m u:alloy:--x /root 2>/dev/null || true
    setfacl -m u:alloy:rx /root/.pm2 2>/dev/null || true
    setfacl -R -m u:alloy:rx "$logs_dir" 2>/dev/null || true
    setfacl -R -d -m u:alloy:r "$logs_dir" 2>/dev/null || true
    find "$logs_dir" -maxdepth 1 -type f -name '*.log' -exec setfacl -m u:alloy:r {} + 2>/dev/null || true
    echo "✓ ACLs: alloy can read $logs_dir"
  else
    chmod 711 /root /root/.pm2 2>/dev/null || true
    chmod 755 "$logs_dir" 2>/dev/null || true
    find "$logs_dir" -maxdepth 1 -type f -name '*.log' -exec chmod o+r {} + 2>/dev/null || true
    echo "✓ chmod: alloy can traverse /root and read $logs_dir (install acl package for setfacl)"
  fi
}

grant_home_pm2_logs() {
  local logs_dir="$1"
  [ -d "$logs_dir" ] || return 0

  chmod o+rX "$logs_dir" 2>/dev/null || true
  find "$logs_dir" -maxdepth 1 -type f -name '*.log' -exec chmod o+r {} + 2>/dev/null || true
  if command -v setfacl &>/dev/null; then
    setfacl -R -m u:alloy:rx "$logs_dir" 2>/dev/null || true
    setfacl -R -d -m u:alloy:r "$logs_dir" 2>/dev/null || true
  fi
  echo "✓ PM2 logs readable: $logs_dir"
}

verify_readable() {
  local label="$1"
  local path="$2"

  if [ ! -e "$path" ]; then
    echo "  — skip $label (not present: $path)"
    return 0
  fi

  if sudo -u alloy head -1 "$path" &>/dev/null; then
    echo "✓ $label: $path"
    return 0
  fi

  echo "✗ $label: alloy cannot read $path"
  FAILED=1
  return 1
}

verify_pm2_log_dir() {
  local logs_dir="$1"
  [ -d "$logs_dir" ] || return 0

  local found=0
  shopt -s nullglob
  for f in "$logs_dir"/*.log; do
    found=1
    verify_readable "PM2" "$f" || true
  done
  shopt -u nullglob

  if [ "$found" -eq 0 ]; then
    echo "  — no *.log files in $logs_dir"
  fi
}

verify_base_alloy_logs() {
  echo ""
  echo "Verifying base Alloy log paths (config.alloy)..."

  verify_readable "system" "/var/log/syslog" || true
  verify_readable "auth" "/var/log/auth.log" || true
  verify_readable "kernel" "/var/log/kern.log" || true

  shopt -s nullglob
  for f in /var/log/app/*.log /var/log/app/*/*.log; do
    verify_readable "app" "$f" || true
  done
  shopt -u nullglob
}

pm2_logs_dirs() {
  local dir
  for dir in /root/.pm2/logs /home/*/.pm2/logs; do
    [ -d "$dir" ] && echo "$dir"
  done
}

find_pm2_log() {
  local name="$1"
  local dir f
  for dir in $(pm2_logs_dirs); do
    f="${dir}/${name}"
    if [ -f "$f" ]; then
      echo "$f"
      return 0
    fi
  done
  return 1
}

verify_expected_pm2_for_server() {
  local -a expected=()
  local name path

  case "$SERVER_NAME" in
    manager)    expected=(manager-out.log manager-error.log) ;;
    worker)     expected=(worker-out.log worker-error.log) ;;
    comparison) expected=(comparison-out.log comparison-error.log) ;;
    pms-api)    expected=(pms-out.log pms-error.log) ;;
    receiver)   return 0 ;; # Docker logs, not PM2 files
    "")
      return 0
      ;;
    *)
      echo "  — unknown server-name '$SERVER_NAME' (skipping expected PM2 file check)"
      return 0
      ;;
  esac

  echo ""
  echo "Verifying expected PM2 logs for server: $SERVER_NAME..."

  for name in "${expected[@]}"; do
    if path=$(find_pm2_log "$name"); then
      verify_readable "expected PM2 ($SERVER_NAME)" "$path" || true
    else
      echo "✗ expected PM2 log missing: $name (not under /root/.pm2/logs or /home/*/.pm2/logs)"
      FAILED=1
    fi
  done

  if [ "$SERVER_NAME" = "comparison" ]; then
    shopt -s nullglob
    local comp_found=0
    for f in /var/log/app/comparison/*.log; do
      comp_found=1
      verify_readable "comparison file" "$f" || true
    done
    shopt -u nullglob
    if [ "$comp_found" -eq 0 ]; then
      echo "  — no optional file logs in /var/log/app/comparison/ (PM2 logs are enough)"
    fi
  fi
}

grant_docker_access
grant_root_pm2_logs
for logs_dir in /home/*/.pm2/logs; do
  grant_home_pm2_logs "$logs_dir"
done

echo ""
echo "Verifying all PM2 log files on this host..."
for logs_dir in $(pm2_logs_dirs); do
  verify_pm2_log_dir "$logs_dir"
done

if ! pm2_logs_dirs | grep -q .; then
  echo "  — no PM2 logs directories found"
fi

verify_base_alloy_logs
verify_expected_pm2_for_server
verify_docker_socket

echo ""
if [ "$FAILED" -ne 0 ]; then
  echo "ERROR: One or more log paths are missing or not readable by alloy"
  exit 1
fi

echo "✓ Alloy permissions verified (PM2 logs, Docker, configured log paths)"
