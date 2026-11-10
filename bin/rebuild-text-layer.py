#!/usr/bin/env python3
"""Rebuild the trial PDF text layer from saved OCR without rerunning OCR."""

import argparse
import csv
import os
import json
import math
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from text_layer_utils import clean_text, fix_dashes, fix_variants, load_glossary

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sanitized_pdf', type=Path)
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('output_pdf', type=Path)
    parser.add_argument('--ocr-stem', default='tiger-sanitized')
    parser.add_argument('--overrides', type=Path,
                        help='TSV columns: page, line_id, ocr_text, corrected_text')
    parser.add_argument('--glossary', type=Path,
                        help='verified terms whose internal OCR spaces should be joined')
    parser.add_argument('--recognized', type=Path,
                        help='TSV from bin/margin-recognize.py to use instead of saved OCR text')
    parser.add_argument('--column', default='margin30', help='column of --recognized to use')
    parser.add_argument('--min-height', type=int, default=0,
                        help='keep saved OCR text for --recognized lines shorter than this many pixels')
    parser.add_argument('--fix-dashes', action='store_true',
                        help='turn hyphens outside Latin words into two-em dashes in vertical lines')
    parser.add_argument('--fix-variants', action='store_true',
                        help='replace dakuten/handakuten misreadings of --glossary terms')
    parser.add_argument('--ndl-src', type=Path,
                        default=Path(os.environ.get('NDLOCR_SRC', Path(__file__).resolve().parents[1] / 'vendor/ndlocr-lite/src')))
    parser.add_argument('--angles', type=Path,
                        help='TSV from prepare-trial-pdf.py --angles; move OCR boxes from the '
                             'unrotated original with each deskewed page image')
    args = parser.parse_args()
    if args.output_pdf.exists() or args.output_pdf.resolve() == args.sanitized_pdf.resolve():
        parser.error('output PDF must be new and separate from the input')
    sys.path.insert(0, str(args.ndl_src.resolve()))
    from ocr import embed_text_layer_pdf
    angles = {}
    if args.angles:
        with args.angles.open(newline='') as handle:
            angles = {int(row['page']): float(row['angle_deg'])
                      for row in csv.DictReader(handle, delimiter='\t')}
    glossary = load_glossary(args.glossary) if args.glossary else []
    if args.fix_variants and not glossary:
        parser.error('--fix-variants needs --glossary')
    variants = Counter()

    overrides = {}
    if args.overrides:
        with args.overrides.open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                key = (int(row['page']), int(row['line_id']))
                if key in overrides or not row['corrected_text']:
                    raise ValueError(f'duplicate or empty override: {key}')
                overrides[key] = row
    recognized = {}
    if args.recognized:
        with args.recognized.open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                recognized[(int(row['page']), int(row['line_id']))] = row

    count = len(PdfReader(args.sanitized_pdf).pages)
    if any(page > count for page in angles):
        raise ValueError('angles TSV has pages beyond the PDF')
    page_results = []
    changed_lines = removed_spaces = 0
    for page in range(1, count + 1):
        path = args.ocr_dir / f'{args.ocr_stem}_{page:05}.json'
        data = json.loads(path.read_text())
        lines = []
        for item in data['contents'][0]:
            box = item['boundingBox']
            x0, x1 = min(p[0] for p in box), max(p[0] for p in box)
            y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
            vertical = item['isVertical'] == 'true'
            original = item['text']
            key = (page, int(item['id']))
            text = original
            if args.recognized:
                row = recognized.pop(key, None)
                if row is None or row['saved'] != original:
                    raise ValueError(f'recognized TSV does not match OCR: {key}')
                if int(row['height']) >= args.min_height:
                    text = row[args.column]
            if key in overrides:
                override = overrides.pop(key)
                if override['ocr_text'] != original:
                    raise ValueError(f'OCR changed for override: {key}')
                text = override['corrected_text']
            else:
                if args.fix_dashes and vertical:
                    text = fix_dashes(text)
                text = clean_text(text, vertical, glossary)
                if args.fix_variants:
                    text = fix_variants(text, glossary, variants)
            if text != original:
                changed_lines += 1
                removed_spaces += (original.count(' ') + original.count('　') -
                                   text.count(' ') - text.count('　'))
            info = data['imginfo']
            if angles.get(page):
                # The text is drawn straight along the box centre line, which already
                # matches the deskewed column, so only move the box centre with the
                # image rotation about its centre (formula from bin/deskew-study.py).
                # Rotating the drawn text as well tilts it against the image.
                angle = math.radians(angles[page])
                cx, cy = info['img_width'] / 2, info['img_height'] / 2
                ox, oy = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
                dx = math.cos(angle) * ox + math.sin(angle) * oy - ox
                dy = -math.sin(angle) * ox + math.cos(angle) * oy - oy
                x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
            lines.append({'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0,
                          'text': text, 'is_vertical': vertical})
        page_results.append({'text_layer_lines': lines, 'img_width': info['img_width'],
                             'img_height': info['img_height']})
    if overrides:
        raise ValueError(f'overrides not found in OCR: {list(overrides)}')
    if recognized:
        raise ValueError(f'recognized lines not found in OCR: {list(recognized)[:5]}')
    embed_text_layer_pdf(str(args.sanitized_pdf), str(args.output_pdf), page_results)
    print(f'{args.output_pdf}: {count} pages, {changed_lines} lines normalized, '
          f'{removed_spaces} spaces removed')
    for (variant, term), count in sorted(variants.items(), key=lambda item: -item[1]):
        print(f'{variant} -> {term}: {count}')


if __name__ == '__main__':
    main()
