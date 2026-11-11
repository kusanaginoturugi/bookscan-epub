#!/usr/bin/env python
"""Compare NDLOCR-Lite JSON runs on the sample from bin/ocr-sample-pages.py.

Run with .venv-ndl/bin/python. A run is a directory name under each book
(ocr.py --output DIR/BOOK/RUN); "saved" means the book's original full OCR.
Lines are paired by box overlap (IoU > 0.5). Gold lines are scored against the
best-matching line on their page with whitespace removed, so the score does not
depend on line ids. Differences are counts, not accuracy: check the image.

usage: bin/compare-ocr-runs.py work/ocr-engine-check saved cpu cuda cuda2
"""

import argparse
import json
import re
from pathlib import Path

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


def load(path):
    lines = []
    for item in json.loads(path.read_text())['contents'][0]:
        xs = [p[0] for p in item['boundingBox']]
        ys = [p[1] for p in item['boundingBox']]
        lines.append(dict(box=(min(xs), min(ys), max(xs), max(ys)), text=item['text']))
    return lines


def iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return w * h / (area(a) + area(b) - w * h)


def page_paths(root, book, name, run):
    if run == 'saved':
        return {p: Path(book['saved']) / f"{book['stem']}_{p:05}.json" for p in book['pages']}
    return {p: root / name / run / f'subset_{i:05}.json' for i, p in enumerate(book['pages'], 1)}


def compare(pa, pb, pages, examples, name):
    s = dict(differ=0, lines_a=0, lines_b=0, same_box=0, moved_box=0, unmatched=0, text_diff=0, edit=0, chars=0)
    for page in pages:
        a, b = load(pa[page]), load(pb[page])
        s['lines_a'] += len(a)
        s['lines_b'] += len(b)
        used, differ = set(), len(a) != len(b)
        for la in a:
            s['chars'] += len(la['text'])
            best, score = None, 0.5
            for j, lb in enumerate(b):
                if j not in used and iou(la['box'], lb['box']) > score:
                    best, score = j, iou(la['box'], lb['box'])
            if best is None:
                s['unmatched'] += 1
                s['edit'] += len(la['text'])
                differ = True
                continue
            used.add(best)
            lb = b[best]
            s['same_box' if lb['box'] == la['box'] else 'moved_box'] += 1
            differ |= lb['box'] != la['box']
            if lb['text'] != la['text']:
                s['text_diff'] += 1
                s['edit'] += lev(la['text'], lb['text'])
                differ = True
                examples.append((name, page, la['text'], lb['text']))
        rest = [j for j in range(len(b)) if j not in used]
        s['unmatched'] += len(rest)
        s['edit'] += sum(len(b[j]['text']) for j in rest)
        s['differ'] += differ
    return s


def gold_score(book, paths):
    edits = chars = exact = 0
    for g in book['gold']:
        expected = WS.sub('', g['expected'])
        lines = [WS.sub('', l['text']) for l in load(paths[g['page']])]
        d = min(lev(expected, t) for t in lines) if lines else len(expected)
        edits, chars, exact = edits + d, chars + len(expected), exact + (d == 0)
    return edits, chars, exact


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path)
    parser.add_argument('runs', nargs='+', help='first run is the baseline')
    args = parser.parse_args()
    manifest = json.loads((args.root / 'manifest.json').read_text())
    base = args.runs[0]
    examples = {}
    print('## pairwise')
    for other in args.runs[1:]:
        examples[other] = []
        for name, book in manifest.items():
            s = compare(page_paths(args.root, book, name, base), page_paths(args.root, book, name, other),
                        book['pages'], examples[other], name)
            print(f"{base} vs {other} {name:9} pages {s['differ']}/{len(book['pages'])} differ; "
                  f"lines {s['lines_a']}/{s['lines_b']}; same box {s['same_box']}, moved {s['moved_box']}, "
                  f"unmatched {s['unmatched']}; text-diff lines {s['text_diff']}; edit {s['edit']}/{s['chars']}")
    print('\n## gold (best-matching line on the page, whitespace removed)')
    for run in args.runs:
        total = [0, 0, 0, 0]
        for name, book in manifest.items():
            e, c, x = gold_score(book, page_paths(args.root, book, name, run))
            total = [total[0] + e, total[1] + c, total[2] + x, total[3] + len(book['gold'])]
            print(f'{run:>8} {name:9} CER {e}/{c} = {e / c:.4f}; exact {x}/{len(book["gold"])}')
        print(f'{run:>8} TOTAL     CER {total[0]}/{total[1]} = {total[0] / total[1]:.4f}; exact {total[2]}/{total[3]}')
    print('\n## text differences')
    for other, items in examples.items():
        print(f'### {base} -> {other} ({len(items)})')
        for name, page, ta, tb in items:
            print(f'{name} p{page}\n  {ta}\n  {tb}')


if __name__ == '__main__':
    main()
