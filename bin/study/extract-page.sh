#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 PDF PAGE" >&2
  exit 2
fi

pdf=$1
page=$2
case "$page" in
  *[!0-9]*|'') echo "PAGE must be a positive integer" >&2; exit 2 ;;
esac

mkdir -p work/sample
prefix=$(printf 'work/sample/page-%03d' "$page")
pdfimages -f "$page" -l "$page" -j "$pdf" "$prefix"
