#!/usr/bin/env bash

set -uo pipefail

component_path="$(dirname "$0")/../package/contents/ui/CompactRepresentation.qml"

if rg -q '^QQC2\.Control \{' "$component_path" \
    && rg -q 'ChartControls\.PieChartControl' "$component_path" \
    && ! rg -q 'ProgressRing' "$component_path"; then
    echo "PASS: compact view uses KDE's PieChartControl and wrapper"
    exit 0
fi

echo "FAIL: compact view still uses the custom ring implementation"
exit 1
