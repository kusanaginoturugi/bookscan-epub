#!/bin/sh
# Build a vertical EPUB 3 from work/searchable/SLUG/lines.tsv via Aozora Bunko text and AozoraEpub3.
# usage: bin/build-epub.sh SOURCE.pdf SLUG [extra args for build-aozora-text.py]
set -eu

BIN=$(cd "$(dirname "$0")" && pwd)
DATA_ROOT=${DATA_ROOT:-$(dirname "$BIN")}
SOURCE=$1
SLUG=$2
shift 2

PY=$DATA_ROOT/.venv-ndl/bin/python
WORK=$DATA_ROOT/work/searchable/$SLUG
EPUB_WORK=$DATA_ROOT/work/epub/$SLUG
OUT=$DATA_ROOT/output/epub
JAR=$DATA_ROOT/vendor/AozoraEpub3/AozoraEpub3.jar

[ -e "$WORK/lines.tsv" ] || { echo "missing $WORK/lines.tsv" >&2; exit 1; }
[ -e "$JAR" ] || { echo "missing $JAR" >&2; exit 1; }

TITLE=$(awk -F'\t' -v s="$SOURCE" '$1 == s { print $2 (($3 != "") ? " - " $3 : "") }' \
  "$BIN/../data/bookscan-catalog.tsv")
[ -n "$TITLE" ] || { echo "not in catalog: $SOURCE" >&2; exit 1; }

mkdir -p "$EPUB_WORK" "$OUT"
TXT=$EPUB_WORK/$TITLE.txt
COVER=$EPUB_WORK/cover.jpg
RUBY=$EPUB_WORK/ruby.tsv
RUBY_HUMAN=$EPUB_WORK/ruby-human.tsv
if [ -z "${SKIP_RUBY:-}" ]; then
  echo "[$(date +%T)] extract ruby: $TITLE"
  "$PY" "$BIN/extract-ruby.py" "$WORK" "$RUBY" --source "$SOURCE"
  if [ -e "$RUBY_HUMAN" ]; then
    set -- --ruby "$RUBY" --ruby-human "$RUBY_HUMAN" "$@"
  else
    set -- --ruby "$RUBY" "$@"
  fi
fi

echo "[$(date +%T)] build aozora text: $TITLE"
"$PY" "$BIN/build-aozora-text.py" "$WORK" "$TXT" --source "$SOURCE" --cover-out "$COVER" "$@"

echo "[$(date +%T)] run AozoraEpub3"
(
  cd "$DATA_ROOT/vendor/AozoraEpub3"
  java -cp AozoraEpub3.jar AozoraEpub3 -enc UTF-8 -c "$COVER" -of -d "$OUT" "$TXT"
)
echo "[$(date +%T)] done: $OUT/$TITLE.epub"
