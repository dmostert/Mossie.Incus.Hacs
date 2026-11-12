#!/bin/sh
# Copies the Python Incus client into the integration (HACS only installs custom_components/incus, so the client must live inside it).
# Usage: tools/vendor.sh /path/to/incus-client-py
set -e
SRC="${1:-../incus-client-py}/incus_client"
DST="$(dirname "$0")/../custom_components/incus/incus_client"
[ -d "$SRC" ] || { echo "client source not found: $SRC" >&2; exit 1; }
rm -rf "$DST"
cp -r "$SRC" "$DST"
find "$DST" -name __pycache__ -prune -exec rm -rf {} \;
echo "vendored $SRC -> $DST"
