#!/bin/sh
# Build one searchable PDF with the Summer Door rules plus deskew:
# prepare (background cleanup + deskew, angles saved) -> NDLOCR-Lite on the
# unrotated original -> margin re-recognition -> text layer rotated with each
# deskewed page (margin30, --min-height 400, --fix-dashes, optional glossary)
# -> finalize. Each step is skipped when its output already exists.
#
# usage: bin/build-searchable-book.sh SOURCE.pdf SLUG [GLOSSARY.tsv]
#   DATA_ROOT  checkout holding input/, output/, work/, .venv-ndl, vendor/ (default: repo)
#   MAX_PAGES  trial with the first N pages
set -eu

BIN=$(cd "$(dirname "$0")" && pwd)
DATA_ROOT=${DATA_ROOT:-$(dirname "$BIN")}
SOURCE=$1
SLUG=$2
GLOSSARY=${3:-}
PY=$DATA_ROOT/.venv-ndl/bin/python
export BOOKSCAN_PDF_DIR=${BOOKSCAN_PDF_DIR:-$DATA_ROOT/input/bookscan-downloads}
export NDLOCR_SRC=${NDLOCR_SRC:-$DATA_ROOT/vendor/ndlocr-lite/src}
WORK=$DATA_ROOT/work/searchable/$SLUG
OUT=$DATA_ROOT/output/searchable
mkdir -p "$WORK" "$OUT"

TITLE=$(awk -F'\t' -v s="$SOURCE" '$1 == s { print $2 (($3 != "") ? " - " $3 : "") }' \
  "$BIN/../data/bookscan-catalog.tsv")
[ -n "$TITLE" ] || { echo "not in catalog: $SOURCE" >&2; exit 1; }

ORIGINAL=$BOOKSCAN_PDF_DIR/$SOURCE
SANITIZED=$WORK/sanitized.pdf
if [ -n "${MAX_PAGES:-}" ]; then
  ORIGINAL=$WORK/original-head.pdf
  [ -e "$ORIGINAL" ] || "$PY" -c 'import sys
from pypdf import PdfReader, PdfWriter
r = PdfReader(sys.argv[1]); w = PdfWriter()
for p in r.pages[:int(sys.argv[3])]: w.add_page(p)
w.write(sys.argv[2])' "$BOOKSCAN_PDF_DIR/$SOURCE" "$ORIGINAL" "$MAX_PAGES"
  set -- --max-pages "$MAX_PAGES"
else
  set --
fi
# ocr.py names per-page files after the PDF it reads.
OCR_STEM=$(basename "${ORIGINAL%.pdf}")

echo "[$(date +%T)] prepare $TITLE"
[ -e "$SANITIZED" ] || "$PY" "$BIN/prepare-trial-pdf.py" "$SOURCE" "$SANITIZED" \
  --angles "$WORK/angles.tsv" "$@" > "$WORK/prepare.log"
PAGES=$("$PY" -c 'import sys; from pypdf import PdfReader; print(len(PdfReader(sys.argv[1]).pages))' "$SANITIZED")

echo "[$(date +%T)] ocr $PAGES pages"
if [ ! -e "$WORK/ocr.done" ]; then
  mkdir -p "$WORK/ocr"
  "$PY" "$NDLOCR_SRC/ocr.py" --sourcepdf "$ORIGINAL" --output "$WORK/ocr" \
    --pdf-output "$WORK/original-ocr.pdf" --pdf-render-dpi 300 > "$WORK/ocr.log" 2>&1
  # ocr.py exits 0 even on errors, so count the per-page JSON files.
  FOUND=$(find "$WORK/ocr" -name "${OCR_STEM}_*.json" | wc -l)
  [ "$FOUND" -eq "$PAGES" ] || { echo "OCR wrote $FOUND/$PAGES pages; see $WORK/ocr.log" >&2; exit 1; }
  touch "$WORK/ocr.done"
fi

echo "[$(date +%T)] margin re-recognition"
if [ ! -e "$WORK/margin.done" ]; then
  rm -f "$WORK/margin.tsv"
  "$PY" "$BIN/margin-recognize.py" "$WORK/margin.tsv" --pdf "$ORIGINAL" \
    --ocr-dir "$WORK/ocr" --ocr-stem "$OCR_STEM" > "$WORK/margin.log" 2>&1
  touch "$WORK/margin.done"
fi

TEXT=$WORK/text-unfinalized.pdf
LOG=$WORK/rebuild.log
if [ -n "$GLOSSARY" ]; then
  TEXT=$WORK/text-unfinalized-glossary.pdf
  LOG=$WORK/rebuild-glossary.log
  set -- --glossary "$GLOSSARY" --fix-variants
else
  set --
fi
echo "[$(date +%T)] text layer"
if [ ! -e "$TEXT" ]; then
  "$PY" "$BIN/rebuild-text-layer.py" "$SANITIZED" "$WORK/ocr" "$TEXT" \
    --ocr-stem "$OCR_STEM" --recognized "$WORK/margin.tsv" --column margin30 \
    --min-height 400 --fix-dashes --angles "$WORK/angles.tsv" "$@" > "$LOG"
  cat "$LOG"
fi

FINAL=$OUT/$TITLE.pdf
[ -z "${MAX_PAGES:-}" ] || FINAL=$WORK/trial-$MAX_PAGES.pdf
[ -z "$GLOSSARY" ] || FINAL="${FINAL%.pdf} 辞書補正.pdf"
[ -e "$FINAL" ] || "$PY" "$BIN/finalize-trial-pdf.py" "$SANITIZED" "$TEXT" "$FINAL" \
  --catalog-source "$SOURCE"
echo "[$(date +%T)] done: $FINAL"
