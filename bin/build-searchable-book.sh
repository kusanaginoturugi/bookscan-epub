#!/bin/sh
# Build one searchable PDF:
# prepare (background cleanup + deskew, angles saved) -> NDLOCR-Lite on the
# unrotated original -> margin re-recognition -> YomiToku -> バ/パ pairs settled
# by word-crop re-reads and the human choices (review page) -> remaining
# NDLOCR-Lite/YomiToku disagreements judged by a local LLM and the human
# verdicts (review page) -> text layer rotated with each deskewed page
# (margin30, --min-height 400, --fix-dashes, --fix-brackets, voicing glossary,
# LLM/human corrections, optional char map) -> finalize.
#
# OCR, margin and YomiToku are skipped when done. The voicing, judge and text
# layer steps rerun when their inputs change (content hash, not file times);
# a finished PDF that would change is moved to OUT/old/ with the date.
# After reviewing, save the page TSVs as WORK/voicing-human.tsv and
# WORK/judge-human.tsv and run again. Review voicing before judge: a new
# glossary changes the text the judge's verdicts were made on.
#
# usage: bin/build-searchable-book.sh SOURCE.pdf SLUG [GLOSSARY.tsv]
#   GLOSSARY   older LLM glossary: use it for --fix-variants and skip the voicing
#              and judge steps (the final gets " 辞書補正")
#   DATA_ROOT  checkout holding input/, output/, work/, review/, .venv-ndl, vendor/ (default: repo)
#   MAX_PAGES  trial with the first N pages
#   NDLOCR_DEVICE  cpu (default) or cuda for OCR, margin re-recognition, YomiToku
#              and word crops; cuda needs onnxruntime-gpu[cuda,cudnn] in .venv-ndl
#   YOMITOKU   yomitoku command (default: DATA_ROOT/vendor/.venv-yomitoku/bin/yomitoku)
#   LLM_URL    OpenAI-compatible server (default http://127.0.0.1:8080/v1), LLM_MODEL,
#              JUDGE_PROMPT (v1+v2 default: v1 decides, v2 with context can veto)
#   SKIP_YOMITOKU=1, SKIP_JUDGE=1  turn those stages off on purpose; without them
#              a missing YomiToku or LLM stops the build. YomiToku off also turns
#              the judge off. A book keeps the stages it was started with.
#   WORK/char-map.tsv  (from, to) characters this book prints differently, e.g. 掴 摑
#   WORK/skip-pages.txt  pages left out of both reviews, e.g. 312-322 (back-matter
#              catalogs are found automatically; the text layer keeps all pages)
#   UPGRADE=1  run the new stages on a work dir made before them (it is otherwise
#              left as it was)
set -eu

BIN=$(cd "$(dirname "$0")" && pwd)
DATA_ROOT=${DATA_ROOT:-$(dirname "$BIN")}
SOURCE=$1
SLUG=$2
GLOSSARY=${3:-}
PY=$DATA_ROOT/.venv-ndl/bin/python
export BOOKSCAN_PDF_DIR=${BOOKSCAN_PDF_DIR:-$DATA_ROOT/input/bookscan-downloads}
export NDLOCR_SRC=${NDLOCR_SRC:-$DATA_ROOT/vendor/ndlocr-lite/src}
WORK=$DATA_ROOT/work/searchable/$SLUG${MAX_PAGES:+-first$MAX_PAGES}
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

# CPU and CUDA read about 2% of lines differently, so one book keeps the device
# of its first OCR run. Work started before NDLOCR_DEVICE existed ran on the CPU.
DEVICE=${NDLOCR_DEVICE:-cpu}
if [ -e "$WORK/device" ]; then
  STARTED=$(cat "$WORK/device")
elif [ -e "$WORK/ocr.done" ]; then
  STARTED=cpu
else
  STARTED=$DEVICE
  echo "$DEVICE" > "$WORK/device"
fi
[ "$STARTED" = "$DEVICE" ] || { echo "$WORK was started with NDLOCR_DEVICE=$STARTED" >&2; exit 1; }

LLM_URL=${LLM_URL:-http://127.0.0.1:8080/v1}
LLM_MODEL=${LLM_MODEL:-dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL}
fail() { echo "$*" >&2; exit 1; }
# The judge's LLM (about 8GB) left loaded in llama-server makes CUDA OCR and
# word crops fail to allocate; unload that one model (others are left alone).
free_gpu() {
  [ "$DEVICE" = cuda ] || return 0
  curl -sf -m 5 "$LLM_URL/models" 2>/dev/null |
    grep -q "\"id\":\"$LLM_MODEL\"[^}]*\"value\":\"loaded\"" || return 0
  curl -sf -m 60 -X POST "${LLM_URL%/v1}/models/unload" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$LLM_MODEL\"}" > /dev/null && echo "[$(date +%T)] unloaded $LLM_MODEL from the GPU"
}
free_gpu

echo "[$(date +%T)] prepare $TITLE"
[ -e "$SANITIZED" ] || "$PY" "$BIN/prepare-trial-pdf.py" "$SOURCE" "$SANITIZED" \
  --angles "$WORK/angles.tsv" "$@" > "$WORK/prepare.log"
PAGES=$("$PY" -c 'import sys; from pypdf import PdfReader; print(len(PdfReader(sys.argv[1]).pages))' "$SANITIZED")

echo "[$(date +%T)] ocr $PAGES pages"
if [ ! -e "$WORK/ocr.done" ]; then
  mkdir -p "$WORK/ocr"
  "$PY" "$NDLOCR_SRC/ocr.py" --sourcepdf "$ORIGINAL" --output "$WORK/ocr" \
    --pdf-output "$WORK/original-ocr.pdf" --pdf-render-dpi 300 --device "$DEVICE" > "$WORK/ocr.log" 2>&1
  # ocr.py exits 0 even on errors, so count the per-page JSON files.
  FOUND=$(find "$WORK/ocr" -name "${OCR_STEM}_*.json" | wc -l)
  [ "$FOUND" -eq "$PAGES" ] || { echo "OCR wrote $FOUND/$PAGES pages; see $WORK/ocr.log" >&2; exit 1; }
  # Without a usable GPU, ocr.py only warns and falls back to the CPU.
  ! grep -q 'CUDAExecutionProvider が利用できない' "$WORK/ocr.log" ||
    { echo "OCR fell back to the CPU; see $WORK/ocr.log" >&2; exit 1; }
  touch "$WORK/ocr.done"
fi

echo "[$(date +%T)] margin re-recognition"
if [ ! -e "$WORK/margin.done" ]; then
  rm -f "$WORK/margin.tsv"
  "$PY" "$BIN/margin-recognize.py" "$WORK/margin.tsv" --pdf "$ORIGINAL" \
    --ocr-dir "$WORK/ocr" --ocr-stem "$OCR_STEM" --device "$DEVICE" > "$WORK/margin.log" 2>&1 ||
    { echo "margin re-recognition failed; see $WORK/margin.log" >&2; exit 1; }
  touch "$WORK/margin.done"
fi

TEXT=$WORK/text-unfinalized.pdf
LOG=$WORK/rebuild.log
FINAL=$OUT/$TITLE.pdf
[ -z "${MAX_PAGES:-}" ] || FINAL=$WORK/trial-$MAX_PAGES.pdf
if [ -n "$GLOSSARY" ]; then
  TEXT=$WORK/text-unfinalized-glossary.pdf
  LOG=$WORK/rebuild-glossary.log
  FINAL="${FINAL%.pdf} 辞書補正.pdf"
fi

# Hash of file names and contents (missing files count too), for rerun checks.
inputs_key() { for f in "$@"; do printf '%s\n' "$f"; [ ! -e "$f" ] || cat "$f"; done | sha256sum | cut -c1-16; }

# Work made before the YomiToku/LLM stages has a text layer but no stages file;
# it is left exactly as it was unless UPGRADE=1.
OLDER=
for f in "$WORK"/text-unfinalized*.pdf; do [ ! -e "$f" ] || OLDER=1; done
if [ ! -e "$WORK/stages" ] && [ -n "$OLDER" ] && [ -z "${UPGRADE:-}" ]; then
  echo "[$(date +%T)] older work dir: YomiToku, voicing and judge stages not run (UPGRADE=1 to run them)"
  [ -e "$FINAL" ] || "$PY" "$BIN/finalize-trial-pdf.py" "$SANITIZED" "$TEXT" "$FINAL" --catalog-source "$SOURCE"
  echo "[$(date +%T)] done: $FINAL"
  exit 0
fi

USE_YOMITOKU=on; USE_JUDGE=on
[ -z "${SKIP_YOMITOKU:-}" ] || { USE_YOMITOKU=off; USE_JUDGE=off; }
[ -z "${SKIP_JUDGE:-}" ] || USE_JUDGE=off
STAGES="yomitoku=$USE_YOMITOKU judge=$USE_JUDGE"
if [ -e "$WORK/stages" ]; then
  [ "$(cat "$WORK/stages")" = "$STAGES" ] || fail "$WORK was started with stages: $(cat "$WORK/stages")"
else
  echo "$STAGES" > "$WORK/stages"
fi
YOMITOKU=${YOMITOKU:-$DATA_ROOT/vendor/.venv-yomitoku/bin/yomitoku}
JUDGE_PROMPT=${JUDGE_PROMPT:-v1+v2}
REVIEW=$DATA_ROOT/review/$(basename "$WORK")

if [ "$USE_YOMITOKU" = on ] && [ ! -e "$WORK/yomitoku.done" ]; then
  echo "[$(date +%T)] yomitoku"
  [ -x "$YOMITOKU" ] || fail "YomiToku not found at $YOMITOKU (set YOMITOKU, or SKIP_YOMITOKU=1)"
  mkdir -p "$WORK/yomitoku"
  "$YOMITOKU" "$ORIGINAL" -f json -o "$WORK/yomitoku" -d "$DEVICE" --dpi 300 --ignore_ruby \
    > "$WORK/yomitoku.log" 2>&1 || fail "YomiToku failed; see $WORK/yomitoku.log"
  FOUND=$(find "$WORK/yomitoku" -name '*_p*.json' | wc -l)
  [ "$FOUND" -eq "$PAGES" ] || fail "YomiToku wrote $FOUND/$PAGES pages; see $WORK/yomitoku.log"
  touch "$WORK/yomitoku.done"
fi
YOMI_ARGS=
[ "$USE_YOMITOKU" = off ] || YOMI_ARGS="--yomitoku-dir $WORK/yomitoku"

# Back-matter catalogs are left out of both reviews (detected, plus WORK/skip-pages.txt).
SKIP_ARGS=--skip-catalog
[ ! -e "$WORK/skip-pages.txt" ] || SKIP_ARGS="$SKIP_ARGS --skip-pages $(tr -d ' \n' < "$WORK/skip-pages.txt")"

# バ/パ: word-crop re-reads decide safe pairs; human choices win.
VOICING_GLOSSARY=${GLOSSARY:-$WORK/voicing-glossary.tsv}
if [ -z "$GLOSSARY" ]; then
  KEY=$(inputs_key "$WORK/voicing-human.tsv" "$WORK/margin.tsv" "$WORK/stages" "$WORK/skip-pages.txt")skip-catalog,line-joins,reread-all,fix-review
  if [ ! -e "$VOICING_GLOSSARY" ] || [ "$(cat "$WORK/voicing.key" 2>/dev/null)" != "$KEY" ]; then
    echo "[$(date +%T)] voicing"
    # shellcheck disable=SC2086
    "$PY" "$BIN/build-voicing-review.py" "$ORIGINAL" "$WORK/ocr" "$REVIEW-voicing" --ocr-stem "$OCR_STEM" \
      --recognized "$WORK/margin.tsv" $YOMI_ARGS $SKIP_ARGS --auto-crop --device "$DEVICE" \
      --human "$WORK/voicing-human.tsv" --glossary-out "$VOICING_GLOSSARY" \
      --corrections-out "$WORK/voicing-corrections.tsv" \
      --title "$TITLE 濁点・半濁点レビュー" > "$WORK/voicing.log" 2>&1 ||
      fail "voicing step failed; see $WORK/voicing.log"
    echo "$KEY" > "$WORK/voicing.key"
  fi
  grep -E '^[0-9]+組|^corrections:|^warning:' "$WORK/voicing.log" || true
fi

WORD_ARGS=
[ ! -e "$WORK/voicing-corrections.tsv" ] || [ -n "$GLOSSARY" ] || WORD_ARGS="--word-corrections $WORK/voicing-corrections.tsv"

# Remaining disagreements: a local LLM decides, human verdicts win.
CORRECTIONS=
if [ "$USE_JUDGE" = on ] && [ -z "$GLOSSARY" ]; then
  CORRECTIONS=$WORK/judge/corrections.tsv
  KEY=$(inputs_key "$VOICING_GLOSSARY" "$WORK/voicing-corrections.tsv" "$WORK/judge-human.tsv" "$WORK/margin.tsv" "$WORK/stages" "$WORK/skip-pages.txt")skip-catalog,line-joins,joined-voicing,lookalike,propagate
  if [ ! -e "$CORRECTIONS" ] || [ "$(cat "$WORK/judge.key" 2>/dev/null)" != "$KEY$LLM_MODEL$JUDGE_PROMPT" ]; then
    echo "[$(date +%T)] judge"
    curl -sf -m 10 "$LLM_URL/models" > /dev/null ||
      fail "no LLM server at $LLM_URL (start llama-server, set LLM_URL, or SKIP_JUDGE=1)"
    "$PY" "$BIN/judge-ocr-disagreements.py" "$ORIGINAL" "$WORK/ocr" "$WORK/yomitoku" "$WORK/judge" \
      "$REVIEW-judge" --ocr-stem "$OCR_STEM" --recognized "$WORK/margin.tsv" \
      --glossary "$VOICING_GLOSSARY" --human "$WORK/judge-human.tsv" $SKIP_ARGS $WORD_ARGS \
      --url "$LLM_URL/chat/completions" --model "$LLM_MODEL" --prompt "$JUDGE_PROMPT" \
      --title "$TITLE LLM判断のレビュー" \
      > "$WORK/judge.log" 2>&1 || fail "judge step failed; see $WORK/judge.log"
    echo "$KEY$LLM_MODEL$JUDGE_PROMPT" > "$WORK/judge.key"
  fi
  grep -E '^unreviewed' "$WORK/judge.log" || true
fi

set -- --glossary "$VOICING_GLOSSARY" --fix-variants
# shellcheck disable=SC2086
[ -z "$WORD_ARGS" ] || set -- "$@" $WORD_ARGS
[ -z "$CORRECTIONS" ] || set -- "$@" --corrections "$CORRECTIONS"
[ ! -e "$WORK/char-map.tsv" ] || set -- "$@" --char-map "$WORK/char-map.tsv"
KEY=$(inputs_key "$VOICING_GLOSSARY" ${WORD_ARGS:+"$WORK/voicing-corrections.tsv"} ${CORRECTIONS:+"$CORRECTIONS"} "$WORK/char-map.tsv" "$WORK/margin.tsv" "$WORK/angles.tsv")line-joins
echo "[$(date +%T)] text layer"
if [ ! -e "$TEXT" ] || [ "$(cat "$WORK/text.key" 2>/dev/null)" != "$KEY" ]; then
  [ ! -e "$TEXT" ] || mv -f "$TEXT" "$TEXT.prev"
  "$PY" "$BIN/rebuild-text-layer.py" "$SANITIZED" "$WORK/ocr" "$TEXT" \
    --ocr-stem "$OCR_STEM" --recognized "$WORK/margin.tsv" --column margin30 \
    --min-height 400 --fix-dashes --fix-brackets --angles "$WORK/angles.tsv" "$@" > "$LOG"
  cat "$LOG"
  echo "$KEY" > "$WORK/text.key"
fi

if [ -e "$FINAL" ] && [ "$(cat "$WORK/final.key" 2>/dev/null)" != "$KEY" ]; then
  # Never drop a finished PDF: keep the previous one under old/ with the date.
  OLD_DIR=$(dirname "$FINAL")/old
  mkdir -p "$OLD_DIR"
  BASE=$(basename "${FINAL%.pdf}")
  KEEP="$OLD_DIR/$BASE $(date +%F).pdf"
  [ ! -e "$KEEP" ] || KEEP="$OLD_DIR/$BASE $(date +%F-%H%M%S).pdf"
  mv -n "$FINAL" "$KEEP"
  echo "[$(date +%T)] previous final kept as $KEEP"
fi
if [ ! -e "$FINAL" ]; then
  "$PY" "$BIN/finalize-trial-pdf.py" "$SANITIZED" "$TEXT" "$FINAL" --catalog-source "$SOURCE"
  echo "$KEY" > "$WORK/final.key"
fi
[ -n "$GLOSSARY" ] || echo "review: $REVIEW-voicing/index.html -> save as $WORK/voicing-human.tsv"
[ "$USE_JUDGE" = off ] || [ -n "$GLOSSARY" ] || echo "review: $REVIEW-judge/index.html -> save as $WORK/judge-human.tsv"
echo "[$(date +%T)] done: $FINAL"
