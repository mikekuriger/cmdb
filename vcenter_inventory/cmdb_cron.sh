#!/usr/bin/env bash
# cmdb_cron.sh  –  Two-tier CMDB sync wrapper
#
# Crontab (as mk7193 on u3):
#   */30 * * * *  /home/mk7193/vcenter/cmdb_cron.sh fast   >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
#   0    2 * * *  /home/mk7193/vcenter/cmdb_cron.sh full   >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
#
# Or run manually:
#   ./cmdb_cron.sh fast   — incremental event-based sync
#   ./cmdb_cron.sh full   — full vcenter_inventory + cmdb_import

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
LOCK_FILE="/tmp/cmdb_sync.lock"
PYTHON="${PYTHON:-python3}"

# DB connection defaults — override via env vars if needed
DB_HOST="${CMDB_HOST:-127.0.0.1}"
DB_PORT="${CMDB_PORT:-3306}"
DB_USER="${CMDB_USER:-root}"
DB_PASS="${CMDB_PASS:-Pay4mysql!}"
DB_NAME="${CMDB_DB:-cmdb}"

# vCenter env files
VC_ENVS="${CMDB_VC_ENVS:-~/vcenter_inventory/na1 ~/vcenter_inventory/ev3}"

# CSV output path for full refresh
CSV_PATH="${CMDB_CSV:-/tmp/vcenter_inventory.csv}"

mkdir -p "${LOG_DIR}"

MODE="${1:-fast}"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# ── lock ──────────────────────────────────────────────────────────────────────
if [ -f "${LOCK_FILE}" ]; then
    PID=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
    echo "[${TIMESTAMP}] [SKIP] Lock file exists (pid=${PID}), another sync is running." >&2
    exit 0
fi
echo $$ > "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

# ── helpers ───────────────────────────────────────────────────────────────────
DB_ARGS="--host ${DB_HOST} --port ${DB_PORT} --user ${DB_USER} --password ${DB_PASS} --db ${DB_NAME}"

run_fast() {
    echo "=== [${TIMESTAMP}] FAST SYNC START ==="
    # shellcheck disable=SC2086
    ${PYTHON} "${SCRIPT_DIR}/cmdb_sync_fast.py" \
        --env ${VC_ENVS} \
        ${DB_ARGS}
    echo "=== FAST SYNC DONE (exit=$?) ==="
}

run_full() {
    echo "=== [${TIMESTAMP}] FULL REFRESH START ==="

    # Step 1 — export CSV from vCenter
    echo "[1/2] Running vcenter_inventory.py..."
    # shellcheck disable=SC2086
    ${PYTHON} "${SCRIPT_DIR}/vcenter_inventory.py" \
        --env ${VC_ENVS} \
        --output "${CSV_PATH}"
    echo "      CSV written to ${CSV_PATH}"

    # Step 2 — import CSV into MySQL
    echo "[2/2] Running cmdb_import.py..."
    ${PYTHON} "${SCRIPT_DIR}/cmdb_import.py" \
        --csv "${CSV_PATH}" \
        ${DB_ARGS}

    echo "=== FULL REFRESH DONE (exit=$?) ==="

    # Optionally remove the CSV after import to avoid stale data confusion
    # rm -f "${CSV_PATH}"
}

# ── dispatch ──────────────────────────────────────────────────────────────────
case "${MODE}" in
    fast)   run_fast ;;
    full)   run_full ;;
    *)
        echo "Usage: $0 {fast|full}"
        exit 1
        ;;
esac
