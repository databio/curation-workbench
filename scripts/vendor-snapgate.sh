#!/usr/bin/env bash
# curation-workbench: re-vendor snapgate's backend from the upstream checkout,
# byte-for-byte (the frontend copy in frontend/src/snapgate is the review UI's;
# re-vendor it the same way when snapgate's frontend changes). Never edit the
# vendored copies; customize through SnapgateHooks, api_key_prefix, and env vars.
#
#   scripts/vendor-snapgate.sh [path/to/snapgate]   # default: assistant/webdev/snapgate
#
# deployment/migrations/001_snapgate.sql is a frozen plain file. A snapgate
# model change needs a new 09x migration; snapgate ships the SQL for its
# optional modules in sql/ (copied here), e.g. sql/useradmin.sql is
# 090_snapgate_useradmin.sql. Run the postgres test tier after re-vendoring:
# a model change shows up there (the hosted app's schema check).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="${1:-$REPO/../../../assistant/webdev/snapgate}"
UPSTREAM="$(cd "$UPSTREAM" && pwd)"

BACKEND="$UPSTREAM/backend/snapgate"
FRONTEND="$UPSTREAM/frontend/src/snapgate"
[[ -f "$BACKEND/__init__.py" ]] || { echo "no snapgate backend at $BACKEND" >&2; exit 1; }

mkdir -p "$REPO/src/snapgate"
rsync -a --delete --include='*.py' --include='README.md' --include='sql/' --include='sql/*.sql' \
    --exclude='*' \
    "$BACKEND/" "$REPO/src/snapgate/"
if [[ "${VENDOR_FRONTEND:-0}" == 1 ]]; then
    [[ -d "$FRONTEND" ]] || { echo "no snapgate frontend at $FRONTEND" >&2; exit 1; }
    rsync -a --delete --exclude='node_modules' "$FRONTEND/" "$REPO/frontend/src/snapgate/"
fi

[[ -f "$REPO/src/snapgate/sql/useradmin.sql" ]] \
    || { echo "snapgate sql/useradmin.sql missing after vendoring" >&2; exit 1; }

version="$(grep -E '^__version__' "$REPO/src/snapgate/__init__.py" | cut -d'"' -f2)"
echo "vendored snapgate $version from $UPSTREAM"
