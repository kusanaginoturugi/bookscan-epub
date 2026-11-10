#!/usr/bin/env python3
"""Check text-layer placement on deskewed pages against OCR of the deskewed image.

For each page in the angles TSV, the page image of the PDF is rendered at 300 dpi
and OCRed again with NDLOCR-Lite. Each text-layer character (PDFium char box)
counts as placed when its centre falls inside a line box from that fresh OCR,
widened by --slack points. Several PDFs can be compared, for example the text
layer rotated with --angles and one built without it. Run with .venv-ndl.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pypdfium2

DPI = 300


def fresh_ocr(pdf, pages, ndl_src):
    boxes = {}
    document = pypdfium2.PdfDocument(str(pdf))
    with tempfile.TemporaryDirectory() as temp:
        images, output = Path(temp, 'images'), Path(temp, 'ocr')
        images.mkdir()
        output.mkdir()
        for page in pages:
            bitmap = document[page - 1].render(scale=DPI / 72)
            bitmap.to_pil().save(images / f'page_{page:05}.png')
        subprocess.run([sys.executable, str(ndl_src / 'ocr.py'), '--sourcedir', str(images),
                        '--output', str(output)], check=True, capture_output=True)
        for page in pages:
            data = json.loads((output / f'page_{page:05}.json').read_text())
            height = document[page - 1].get_height()
            boxes[page] = []
            for item in data['contents'][0]:
                xs = [p[0] * 72 / DPI for p in item['boundingBox']]
                ys = [height - p[1] * 72 / DPI for p in item['boundingBox']]
                boxes[page].append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def placed(pdf, page, boxes, slack):
    textpage = pypdfium2.PdfDocument(str(pdf))[page - 1].get_textpage()
    inside = total = 0
    for index in range(textpage.count_chars()):
        if textpage.get_text_range(index, 1).isspace():
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        x, y = (left + right) / 2, (bottom + top) / 2
        total += 1
        inside += any(x0 - slack <= x <= x1 + slack and y0 - slack <= y <= y1 + slack
                      for x0, y0, x1, y1 in boxes)
    return inside, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('angles', type=Path)
    parser.add_argument('pdfs', type=Path, nargs='+', help='first PDF supplies the page images')
    parser.add_argument('--slack', type=float, default=1.0, help='points added around OCR line boxes')
    parser.add_argument('--ndl-src', type=Path, default=Path(os.environ.get(
        'NDLOCR_SRC', Path(__file__).resolve().parents[1] / 'vendor/ndlocr-lite/src')))
    args = parser.parse_args()
    with args.angles.open(newline='') as handle:
        angles = {int(row['page']): float(row['angle_deg'])
                  for row in csv.DictReader(handle, delimiter='\t')}
    if not angles:
        print('no deskewed pages')
        return
    boxes = fresh_ocr(args.pdfs[0], sorted(angles), args.ndl_src)
    totals = {pdf: [0, 0] for pdf in args.pdfs}
    for page in sorted(angles):
        cells = []
        for pdf in args.pdfs:
            inside, total = placed(pdf, page, boxes[page], args.slack)
            totals[pdf][0] += inside
            totals[pdf][1] += total
            cells.append(f'{inside}/{total}')
        print(f'page {page} ({angles[page]:+.2f}°): ' + '  '.join(cells))
    for pdf, (inside, total) in totals.items():
        print(f'{pdf.name}: {inside}/{total} = {inside / max(total, 1):.1%} inside fresh OCR lines')


if __name__ == '__main__':
    main()
