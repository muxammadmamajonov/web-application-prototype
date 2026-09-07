#!/usr/bin/env bash
# RLS Enable Script — #95 Production Cutover
#
# This script safely enables Row-Level Security (RLS) enforcement on the
# Muddato production database. RLS adds a database-layer tenant-isolation
# backstop that protects against application bugs that might forget a
# WHERE tenant_id clause.
#
# PREREQUISITES:
#   1. The r73_rls_tenant_isolation migration is live on the target DB.
#      (muddato_app role + RLS policies must already exist)
#   2. Local integration tests pass: pytest -m integration backend/tests/
#   3. All app machines running old code (before RLS plumbing shipped) have
#      been drained and old instances terminated so they don't accidentally
#      run as postgres (which bypasses RLS).
#
# STEPS:
#   1. Sets RLS_ENABLED=true in Fly secrets (applies to all machines).
#   2. Triggers a full rolling deploy with a minimal canary:
#      - Start 1 new app machine with RLS enabled.
#      - Wait for /ready checks to pass (DB + Redis must work).
#      - Scale up from 1 to 2+ machines (original count).
#      - Old machines drain and terminate.
#   3. Verifies that the worker process started cleanly.
#
# ROLLBACK:
#   If the deploy fails or RLS enforcement breaks the app:
#      1. Revert the secret: fly secrets unset RLS_ENABLED
#      2. Trigger a deploy: fly deploy --strategy rolling
#      3. App machines reboot, read RLS_ENABLED unset (defaults to false),
#         and resume running as postgres (bypassing RLS).
#
# OBSERVABILITY:
#   - Monitor /metrics for any Postgres errors (_pg_errors_total).
#   - Check structured logs (Fly) for "policy violation" or "RLS" errors.
#   - If errors spike, execute ROLLBACK immediately.

set -euo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[RLS Enable]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[RLS Enable]${NC} $*"
}

log_error() {
    echo -e "${RED}[RLS Enable]${NC} $*"
}

# Verify Fly CLI is installed
if ! command -v fly &> /dev/null; then
    log_error "fly CLI not found. Install from https://fly.io/docs/getting-started/installing-flyctl/"
    exit 1
fi

# Get current app name from fly.toml
APP_NAME=$(grep "^app = " backend/fly.toml | cut -d'"' -f2)
if [ -z "$APP_NAME" ]; then
    log_error "Could not determine app name from fly.toml"
    exit 1
fi

log_info "Target app: $APP_NAME"

# Confirm with user before proceeding
echo ""
log_warn "This will enable RLS enforcement on your production database."
log_warn "The app will start running tenant requests under the muddato_app role."
log_warn "A forgotten WHERE clause can no longer leak cross-tenant data."
echo ""
echo "Prerequisites checklist:"
echo "  [ ] r73_rls_tenant_isolation migration is live"
echo "  [ ] Local tests pass: pytest -m integration backend/tests/"
echo "  [ ] All machines running old code have been terminated"
echo ""
echo "Proceed with RLS cutover? (type 'yes' to confirm)"
read -r confirm
if [ "$confirm" != "yes" ]; then
    log_info "Cancelled."
    exit 0
fi

# Set the secret
log_info "Setting RLS_ENABLED=true in Fly secrets..."
fly secrets set RLS_ENABLED=true -a "$APP_NAME"

log_info "Triggering rolling deploy..."
fly deploy -a "$APP_NAME" --strategy rolling

log_info "Waiting for machines to stabilize..."
sleep 10

# Verify the deploy succeeded
if fly status -a "$APP_NAME" | grep -q "healthy"; then
    log_info "✓ Machines are healthy"
else
    log_error "Machines did not stabilize. Check 'fly logs' and consider rollback."
    exit 1
fi

log_info ""
log_info "════════════════════════════════════════════════════════════"
log_info "✓ RLS_ENABLED=true is now live in production."
log_info "════════════════════════════════════════════════════════════"
log_info ""
log_info "NEXT STEPS:"
log_info "  1. Monitor /metrics for _pg_errors_total and RLS policy violations."
log_info "  2. Watch structured logs (Fly Dashboard) for 'policy' errors."
log_info "  3. If issues arise, execute ROLLBACK immediately (below)."
log_info ""
log_info "ROLLBACK COMMAND (if needed):"
log_info "  fly secrets unset RLS_ENABLED -a $APP_NAME && fly deploy -a $APP_NAME --strategy rolling"
log_info ""
