#!/usr/bin/env python
"""Cut a fixed OCR test sample out of three books for comparing devices or engines.

Run with .venv-ndl/bin/python. Each book gets subset.pdf with every page that has
image-verified gold lines plus 20 random body pages (fixed seed), and
manifest.json maps subset page N to its PDF page and keeps the gold lines.
Book text stays under work/ and is not committed.

usage: bin/study/ocr-sample-pages.py work/ocr-engine-check
"""

import argparse
import csv
import json
import random
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT.parent / 'bookscan/pdf'
BOOKS = {
    'tiger': dict(pdf=ROOT / 'output/trial/tiger-sanitized.pdf',
                  saved=ROOT / 'work/trial/tiger-full', stem='tiger-sanitized',
                  gold=['work/eval/tiger-gold.tsv']),
    'infinity': dict(pdf=PDF / '0101-20150604103726242_check.pdf',
                     saved=ROOT / 'work/trial/infinity-upper-ocr', stem='0101-20150604103726242_check',
                     gold=['work/margin/gold-random.tsv', 'work/margin/gold-existing.tsv']),
    'summer': dict(pdf=PDF / '0101-20150604095752068_check.pdf',
                   saved=ROOT / 'work/trial/summer-door-ocr', stem='0101-20150604095752068_check',
                   gold=['work/margin/summer-door-gold-random.tsv']),
}
SEED = 20261111


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    manifest = {}
    for name, book in BOOKS.items():
        reader = PdfReader(book['pdf'])
        total = len(reader.pages)
        gold = []
        for path in book['gold']:
            with (ROOT / path).open(newline='') as handle:
                for row in csv.DictReader(handle, delimiter='\t'):
                    gold.append(dict(page=int(row['page']), expected=row['expected'],
                                     src=Path(path).name, group=row.get('group', row.get('source', ''))))
        pages = {g['page'] for g in gold}
        pool = [p for p in range(11, total - 10) if p not in pages]
        pages = sorted(pages | set(random.Random(SEED).sample(pool, 20)))
        writer = PdfWriter()
        for page in pages:
            writer.add_page(reader.pages[page - 1])
        dest = args.output / name
        dest.mkdir(parents=True, exist_ok=True)
        writer.write(dest / 'subset.pdf')
        manifest[name] = dict(pages=pages, gold=gold, saved=str(book['saved']), stem=book['stem'])
        print(f'{name}: {len(pages)} of {total} pages, {len(gold)} gold lines')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
