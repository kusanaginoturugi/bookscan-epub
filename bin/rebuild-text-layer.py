#!/usr/bin/env python3
"""Rebuild the trial PDF text layer from saved OCR without rerunning OCR."""

import argparse
import csv
import json
import sys
from pathlib import Path

from pypdf import PdfReader

from text_layer_utils import clean_text, fix_dashes, load_glossary

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
    parser.add_argument('--fix-dashes', action='store_true',
                        help='turn hyphens outside Latin words into two-em dashes in vertical lines')
    parser.add_argument('--ndl-src', type=Path,
                        default=Path('../bookscan/vendor/ndlocr-lite/src'))
    args = parser.parse_args()
    if args.output_pdf.exists() or args.output_pdf.resolve() == args.sanitized_pdf.resolve():
        parser.error('output PDF must be new and separate from the input')
    sys.path.insert(0, str(args.ndl_src.resolve()))
    from ocr import embed_text_layer_pdf
    glossary = load_glossary(args.glossary) if args.glossary else []

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
            if text != original:
                changed_lines += 1
                removed_spaces += (original.count(' ') + original.count('　') -
                                   text.count(' ') - text.count('　'))
            lines.append({'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0,
                          'text': text, 'is_vertical': vertical})
        info = data['imginfo']
        page_results.append({'text_layer_lines': lines, 'img_width': info['img_width'],
                             'img_height': info['img_height']})
    if overrides:
        raise ValueError(f'overrides not found in OCR: {list(overrides)}')
    if recognized:
        raise ValueError(f'recognized lines not found in OCR: {list(recognized)[:5]}')
    embed_text_layer_pdf(str(args.sanitized_pdf), str(args.output_pdf), page_results)
    print(f'{args.output_pdf}: {count} pages, {changed_lines} lines normalized, '
          f'{removed_spaces} spaces removed')


if __name__ == '__main__':
    main()
