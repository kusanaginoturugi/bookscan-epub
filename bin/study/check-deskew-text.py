#!/usr/bin/env python3
"""Check text-layer placement on deskewed pages against OCR of the deskewed image.

For each page in the angles TSV, the page image is rendered at 300 dpi and
OCRed again with NDLOCR-Lite. For every tall vertical line of that fresh OCR,
the text-layer characters (PDFium char boxes) on its centre line are collected
and the offset of their vertical midpoint from the line box midpoint is taken.
If the OCR boxes were not moved with the page rotation, this offset grows
linearly across the page: the slope dy/dx is about sin(angle). A correct
placement gives a slope near 0. Several PDFs with the same page images can be
compared. Run with .venv-ndl.
"""

import argparse
import csv
import json
import math
import os
import statistics
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


def slope(pdf, page, boxes, min_height=100, near=5):
    textpage = pypdfium2.PdfDocument(str(pdf))[page - 1].get_textpage()
    chars = []
    for index in range(textpage.count_chars()):
        if textpage.get_text_range(index, 1).isspace():
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        chars.append(((left + right) / 2, (bottom + top) / 2))
    points = []
    for x0, y0, x1, y1 in boxes:
        if y1 - y0 < min_height:
            continue
        centre = (x0 + x1) / 2
        ys = [y for x, y in chars if abs(x - centre) < near and y0 - 15 <= y <= y1 + 15]
        if len(ys) >= 10:
            points.append((centre, (max(ys) + min(ys)) / 2 - (y0 + y1) / 2))
    if len(points) < 3:
        return None, len(points)
    mean_x = statistics.mean(x for x, _ in points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    return sum((x - mean_x) * d for x, d in points) / spread, len(points)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('angles', type=Path)
    parser.add_argument('pdfs', type=Path, nargs='+', help='first PDF supplies the page images')
    parser.add_argument('--ndl-src', type=Path, default=Path(os.environ.get(
        'NDLOCR_SRC', Path(__file__).resolve().parents[2] / 'vendor/ndlocr-lite/src')))
    args = parser.parse_args()
    with args.angles.open(newline='') as handle:
        angles = {int(row['page']): float(row['angle_deg'])
                  for row in csv.DictReader(handle, delimiter='\t')}
    if not angles:
        print('no deskewed pages')
        return
    boxes = fresh_ocr(args.pdfs[0], sorted(angles), args.ndl_src)
    for page, angle in sorted(angles.items()):
        cells = []
        for pdf in args.pdfs:
            value, columns = slope(pdf, page, boxes[page])
            cells.append(f'{pdf.name}: ' + ('n/a' if value is None else f'{value:+.4f}')
                         + f' ({columns} columns)')
        unrotated = math.sin(math.radians(abs(angle)))
        print(f'page {page} ({angle:+.2f}°, unmoved boxes give about ±{unrotated:.4f}): '
              + '; '.join(cells))


if __name__ == '__main__':
    main()
