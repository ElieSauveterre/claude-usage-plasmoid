#!/usr/bin/env bash

set -uo pipefail

ui_dir="$(dirname "$0")/../package/contents/ui"

if rg -q '"#d97757"' "$ui_dir/CompactRepresentation.qml" \
    && rg -q 'accentColor: "#d97757"' "$ui_dir/FullRepresentation.qml" \
    && ! rg -q -i '#8b5cf6|codex' "$ui_dir"; then
    echo "PASS: UI uses Claude orange and no Codex leftovers"
    exit 0
fi

echo "FAIL: UI accent is not Claude orange or still references Codex"
exit 1
