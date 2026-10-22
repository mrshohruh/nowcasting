#!/usr/bin/env bash
# Shadow run — internal test of the full nowcasting pipeline.
#
# Usage:
#   ./scripts/shadow_run.sh                  # uses today's date
#   ./scripts/shadow_run.sh 2027-02-01       # specific as-of date
#
# Output goes to _shadow/<date>/ with site, PDFs, and logs.

set -euo pipefail

AS_OF="${1:-$(date +%Y-%m-%d)}"
OUT_DIR="_shadow/${AS_OF}"
LOG_FILE="${OUT_DIR}/shadow_run.log"

echo "=== Shadow Run ==="
echo "As-of date:  ${AS_OF}"
echo "Output dir:  ${OUT_DIR}"
echo ""

mkdir -p "${OUT_DIR}"

# Run the full pipeline
python scripts/produce.py \
  --store data/store \
  --state-dir state/ \
  --out "${OUT_DIR}/site" \
  --as-of "${AS_OF}" \
  -v 2>&1 | tee "${LOG_FILE}"

echo ""
echo "=== Shadow Run Complete ==="
echo "Dashboard:   ${OUT_DIR}/site/index.html"
echo "PDF (EN):    ${OUT_DIR}/site/note_en.pdf"
echo "PDF (UZ):    ${OUT_DIR}/site/note_uz.pdf"
echo "Log:         ${LOG_FILE}"
echo ""
echo "Review checklist:"
echo "  [ ] Headline number looks plausible (within historical range)"
echo "  [ ] Uncertainty band narrows at later horizons"
echo "  [ ] News decomposition components sum to total change"
echo "  [ ] All 8 dashboard pages render without errors"
echo "  [ ] Data monitor shows expected availability pattern"
echo "  [ ] PDFs render correctly in both languages"
echo "  [ ] Sector contributions sum approximately to DFM headline"
