#!/usr/bin/env bash

set -uo pipefail

component_path="$(dirname "$0")/../package/contents/ui/CompactRepresentation.qml"

if rg -q '^import Qt5Compat\.GraphicalEffects$' "$component_path" \
    && rg -q 'layer\.enabled:' "$component_path" \
    && rg -q 'layer\.effect: Glow \{' "$component_path" \
    && rg -q 'color: Kirigami\.Theme\.backgroundColor' "$component_path"; then
    echo "PASS: compact percentage uses KDE's background glow"
    exit 0
fi

echo "FAIL: compact percentage lacks KDE's background glow"
exit 1
