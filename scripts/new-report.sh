#!/bin/bash
# Create a new report directory from template
# Usage: ./scripts/new-report.sh HYP-200 "Unified Diagnostic Panel"

set -euo pipefail

TASK_ID="${1:?Usage: $0 <HYP-XXX> <title>}"
TITLE="${2:?Usage: $0 <HYP-XXX> <title>}"
REPORT_DIR="reports/${TASK_ID}"
DATE=$(date +%Y-%m-%d)

cd "$(dirname "$0")/.."

if [ -d "$REPORT_DIR" ]; then
  echo "Report directory $REPORT_DIR already exists"
  exit 1
fi

mkdir -p "$REPORT_DIR"
cp _template/report.html "$REPORT_DIR/index.html"

# Replace placeholders
sed -i '' \
  -e "s/{{TASK_ID}}/${TASK_ID}/g" \
  -e "s/{{TITLE}}/${TITLE}/g" \
  -e "s/{{DATE}}/${DATE}/g" \
  "$REPORT_DIR/index.html"

echo "Created $REPORT_DIR/index.html"
echo "Edit REPORT_DATA in the file to populate the report."
