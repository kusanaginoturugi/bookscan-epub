#!/usr/bin/env python
"""Compare bin/margin-recognize.py runs on the sample from bin/ocr-sample-pages.py.

Run with .venv-ndl/bin/python. Each run is DIR/BOOK/margin-RUN.tsv. Lines get the
text bin/build-searchable-book.sh would place: margin30 for lines at least 400px
high, the saved OCR otherwise, then --fix-dashes and --fix-brackets on vertical
lines (double quotes paired over the page's vertical lines). Gold lines
are scored against the best-matching line on their page, whitespace removed.

usage: bin/study/score-margin-runs.py work/ocr-engine-check ocrcpu-reccpu ocrcuda-reccuda
"""

import argparse
import csv
import json
import re
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # bin/, for text_layer_utils
from text_layer_utils import fix_brackets, fix_dashes, fix_quotes

WS = re.compile(r'\s+')


def lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def production(path):
    rows = {}
    with path.open(newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            text = row['margin30'] if int(row['height']) >= 400 else row['saved']
            vertical = row['vertical'] == '1'
            if vertical:
                text = fix_brackets(fix_dashes(text))
            rows.setdefault(int(row['page']), []).append([text, vertical])
    pages = {}
    for page, lines in rows.items():
        vertical = [line for line in lines if line[1]]
        for line, text in zip(vertical, fix_quotes([line[0] for line in vertical])[0]):
            line[0] = text
        pages[page] = [WS.sub('', text) for text, _ in lines]
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    parser.add_argument('runs', nargs='+', help='first run is the baseline')
    args = parser.parse_args()
    manifest = json.loads((args.root / 'manifest.json').read_text())
    texts = {run: {name: production(args.root / name / f'margin-{run}.tsv') for name in manifest}
             for run in args.runs}
    base = args.runs[0]
    diffs = []
    print('## line differences against the baseline (whitespace ignored)')
    for other in args.runs[1:]:
        for name, book in manifest.items():
            lines = changed = edit = chars = 0
            for i, page in enumerate(book['pages'], 1):
                la, lb = texts[base][name].get(i, []), texts[other][name].get(i, [])
                lines += len(la)
                chars += sum(map(len, la))
                if len(la) != len(lb):
                    changed += 1
                    edit += lev(''.join(la), ''.join(lb))
                    continue
                for x, y in zip(la, lb):
                    if x != y:
                        changed += 1
                        edit += lev(x, y)
                        diffs.append((other, name, page, x, y))
            print(f'{base} vs {other} {name:9} lines {lines}, changed {changed}, edit {edit}/{chars}')
    print('\n## gold (best-matching line on the page, whitespace removed)')
    for run in args.runs:
        total = [0, 0, 0, 0]
        for name, book in manifest.items():
            e = c = x = 0
            for g in book['gold']:
                expected = WS.sub('', g['expected'])
                lines = texts[run][name].get(book['pages'].index(g['page']) + 1, [])
                d = min(lev(expected, t) for t in lines) if lines else len(expected)
                e, c, x = e + d, c + len(expected), x + (d == 0)
            total = [total[0] + e, total[1] + c, total[2] + x, total[3] + len(book['gold'])]
            print(f'{run:16} {name:9} CER {e}/{c} = {e / c:.4f}; exact {x}/{len(book["gold"])}')
        print(f'{run:16} TOTAL     CER {total[0]}/{total[1]} = {total[0] / total[1]:.4f}; exact {total[2]}/{total[3]}')
    print('\n## differing lines')
    for other, name, page, x, y in diffs:
        print(f'{base} -> {other} {name} p{page}\n  {x}\n  {y}')


if __name__ == '__main__':
    main()
