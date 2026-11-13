#!/bin/sh
# Render the Markdown docs to HTML for local browsing. Links to .md files become
# links to the .html written here, so artifacts.html never opens raw Markdown.
#
# usage: bin/build-docs-html.sh
set -eu

cd "$(dirname "$0")/.."
LUA=bin/md-links-to-html.lua

pandoc -s --toc -M lang=ja -M title=検索可能PDFとレビューの目次 --lua-filter "$LUA" \
  artifacts.md -o artifacts.html
for md in README.md docs/progress.md docs/epub-plan.md data/summer-door-note-candidates.md; do
  pandoc -s --toc -M lang=ja --metadata pagetitle="$(sed -n '1s/^# //p' "$md")" \
    --lua-filter "$LUA" "$md" -o "${md%.md}.html"
done
