#!/usr/bin/env python
"""Score OCR engines on the sample from bin/ocr-sample-pages.py, independent of line splits.

Run with .venv-ndl/bin/python. Engines are KIND:RUN under each book directory:
  ndl:RUN       ocr.py JSON in BOOK/RUN/subset_NNNNN.json (lines in file order)
  margin:RUN    BOOK/margin-RUN.tsv, the text bin/build-searchable-book.sh places
                (margin30 for lines >= 400px, saved OCR otherwise, --fix-dashes)
  yomitoku:RUN  yomitoku -f json in BOOK/RUN/BOOK_subset_pN.json (paragraph order)
Text is NFKC-normalized, dashes are unified and whitespace is removed for every
engine and the gold alike. Each gold line is scored by the edit distance to its
best-matching substring of the page text, so engines that split lines
differently are not penalized. Totals are also split by the gold group
(changed/same/... from work/margin, "-" for the tiger sample).

--diff ENGINE_A ENGINE_B lists long lines of A (>= 20 chars) on the random
pages (no gold) whose best match in B's page text differs, for image review.

usage: bin/score-ocr-engines.py work/ocr-engine-check margin:ocrcpu-reccpu yomitoku:yomitoku
"""

import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from text_layer_utils import fix_dashes

DASHES = re.compile('[―—–─‐-]+')
WS = re.compile(r'\s+')
# Same character, different code point: YomiToku writes U+00B7 for the
# katakana middle dot and curly quotes for 〝〟.
STYLE = str.maketrans({'·': '・', '“': '〝', '”': '〟'})


def norm(text):
    return DASHES.sub('―', WS.sub('', unicodedata.normalize('NFKC', text).translate(STYLE)))


def substring_distance(pattern, text):
    """Smallest edit distance between pattern and any substring of text."""
    prev = [0] * (len(text) + 1)
    for i, cp in enumerate(pattern, 1):
        cur = [i]
        for j, ct in enumerate(text, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cp != ct)))
        prev = cur
    return min(prev)


def best_substring(pattern, text):
    """The substring of text closest to pattern (ends where the distance is smallest)."""
    prev = [0] * (len(text) + 1)
    for i, cp in enumerate(pattern, 1):
        cur = [i]
        for j, ct in enumerate(text, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cp != ct)))
        prev = cur
    end = prev.index(min(prev))
    starts = range(max(0, end - len(pattern) - 10), end + 1)
    return min((text[s:end] for s in starts), key=lambda part: edit_distance(part, pattern))


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def page_lines(root, name, book, engine):
    """{subset page index: [normalized line text in reading order]}"""
    kind, run = engine.split(':', 1)
    pages = {}
    for i in range(1, len(book['pages']) + 1):
        if kind == 'ndl':
            items = json.loads((root / name / run / f'subset_{i:05}.json').read_text())['contents'][0]
            pages[i] = [item['text'] for item in items]
        elif kind == 'yomitoku':
            data = json.loads((root / name / run / f'{name}_subset_p{i}.json').read_text())
            paragraphs = sorted(data['paragraphs'], key=lambda p: p['order'])
            pages[i] = [line for p in paragraphs for line in (p['contents'] or '').split('\n')]
        else:
            pages[i] = []
    if kind == 'margin':
        with (root / name / f'margin-{run}.tsv').open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                text = row['margin30'] if int(row['height']) >= 400 else row['saved']
                pages[int(row['page'])].append(fix_dashes(text) if row['vertical'] == '1' else text)
    return {i: [norm(t) for t in lines] for i, lines in pages.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    parser.add_argument('engines', nargs='+')
    parser.add_argument('--diff', nargs=2, metavar=('A', 'B'))
    args = parser.parse_args()
    manifest = json.loads((args.root / 'manifest.json').read_text())
    engines = list(dict.fromkeys(args.engines + (args.diff or [])))
    texts = {e: {name: page_lines(args.root, name, book, e) for name, book in manifest.items()} for e in engines}

    print('## gold, best substring of the page text (NFKC, dashes unified, no whitespace)')
    for engine in args.engines:
        by_group = defaultdict(lambda: [0, 0, 0, 0])
        for name, book in manifest.items():
            for g in book['gold']:
                expected = norm(g['expected'])
                page_text = ''.join(texts[engine][name][book['pages'].index(g['page']) + 1])
                d = substring_distance(expected, page_text)
                for key in (f'{name}', f'group={g["group"] or "-"}', 'TOTAL'):
                    s = by_group[key]
                    s[0], s[1], s[2], s[3] = s[0] + d, s[1] + len(expected), s[2] + (d == 0), s[3] + 1
        for key, (e, c, x, n) in by_group.items():
            print(f'{engine:22} {key:16} CER {e:4}/{c:4} = {e / c:.4f}; exact {x}/{n}')

    if args.diff:
        a, b = args.diff
        print(f'\n## {a} lines (>= 20 chars) whose best match in {b} differs, random pages only')
        shown = total = 0
        for name, book in manifest.items():
            gold_pages = {g['page'] for g in book['gold']}
            for i, page in enumerate(book['pages'], 1):
                if page in gold_pages:
                    continue
                other = ''.join(texts[b][name][i])
                for line in texts[a][name][i]:
                    if len(line) < 20:
                        continue
                    total += 1
                    d = substring_distance(line, other)
                    if d:
                        shown += 1
                        print(f'#{shown} {name} p{page} d={d}\n  A {line}\n  B {best_substring(line, other)}')
        print(f'{shown}/{total} lines differ')


if __name__ == '__main__':
    main()
