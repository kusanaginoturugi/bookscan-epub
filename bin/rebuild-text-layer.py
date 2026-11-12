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

from text_layer_utils import (apply_corrections, clean_text, fix_brackets, fix_dashes, fix_quotes,
                              fix_variants, line_joins, load_corrections, load_glossary)

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
    parser.add_argument('--fix-brackets', action='store_true',
                        help='in vertical lines turn <> into 〈〉 and pair ASCII double quotes '
                             'into 〝〟 per page (pages whose quotes do not pair up are kept)')
    parser.add_argument('--corrections', type=Path,
                        help='TSV from judge-ocr-disagreements.py (page, line_id, left, from, to, right); '
                             'applied after the other fixes, before quote pairing')
    parser.add_argument('--word-corrections', type=Path,
                        help='TSV like --corrections from build-voicing-review.py --corrections-out; '
                             'applied after --fix-brackets, before --glossary')
    parser.add_argument('--char-map', type=Path,
                        help='TSV (from, to) of characters this book prints differently from OCR, '
                             'e.g. 掴 -> 摑; applied after --corrections')
    parser.add_argument('--fix-variants', action='store_true',
                        help='replace dakuten/handakuten misreadings of --glossary terms')
    parser.add_argument('--ndl-src', type=Path,
                        default=Path(os.environ.get('NDLOCR_SRC', Path(__file__).resolve().parents[1] / 'vendor/ndlocr-lite/src')))
    parser.add_argument('--dump-lines', type=Path,
                        help='write a TSV of every line at each stage (raw OCR, margin re-read, rules, '
                             'corrections, final) for proofreading samples')
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

    corrections = {}
    if args.corrections:
        with args.corrections.open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                corrections.setdefault((int(row['page']), int(row['line_id'])), []).append(row)
    applied = 0
    word_corrections = load_corrections(args.word_corrections)
    word_applied = 0
    char_map = {}
    if args.char_map:
        with args.char_map.open(newline='') as handle:
            char_map = {row['from']: row['to'] for row in csv.DictReader(handle, delimiter='\t')}
        if any(len(k) != 1 or len(v) != 1 for k, v in char_map.items()):
            raise ValueError('--char-map maps single characters')
    char_map = str.maketrans(char_map)
    mapped = 0
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
    dumped = []
    changed_lines = removed_spaces = quotes = 0
    unpaired_pages = []
    for page in range(1, count + 1):
        path = args.ocr_dir / f'{args.ocr_stem}_{page:05}.json'
        data = json.loads(path.read_text())
        lines = []
        joins = line_joins([item['text'] for item in data['contents'][0]])
        for position, item in enumerate(data['contents'][0]):
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
            stages = {'raw': original, 'margin': text}
            if key in overrides:
                override = overrides.pop(key)
                if override['ocr_text'] != original:
                    raise ValueError(f'OCR changed for override: {key}')
                text = override['corrected_text']
                stages['rules'] = stages['judged'] = text
            else:
                if args.fix_dashes and vertical:
                    text = fix_dashes(text)
                if args.fix_brackets and vertical:
                    text = fix_brackets(text)
                rows = word_corrections.pop(key, [])
                text = apply_corrections(text, rows, key)
                word_applied += len(rows)
                text = clean_text(text, vertical, glossary)
                if args.fix_variants:
                    text = fix_variants(text, glossary, variants, joins[position])
                stages['rules'] = text
                for row in corrections.pop(key, []):
                    # The surrounding characters must still be there, so a stale
                    # correction stops the build instead of landing elsewhere.
                    old = row['left'] + row['from'] + row['right']
                    if text.count(old) != 1:
                        raise ValueError(f'correction does not match line {key}: {row["id"]}')
                    text = text.replace(old, row['left'] + row['to'] + row['right'])
                    applied += 1
                stages['judged'] = text
                if char_map:
                    before = text
                    text = text.translate(char_map)
                    mapped += sum(a != b for a, b in zip(before, text))
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
            box = (min(p[0] for p in item['boundingBox']), min(p[1] for p in item['boundingBox']),
                   max(p[0] for p in item['boundingBox']), max(p[1] for p in item['boundingBox']))
            lines.append({'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0,
                          'text': text, 'is_vertical': vertical, 'original': original,
                          'dump': dict(stages, page=page, line_id=key[1], vertical=int(vertical),
                                       box=','.join(map(str, box)))})
        if args.fix_brackets:
            vertical_lines = [line for line in lines if line['is_vertical']]
            texts, replaced = fix_quotes([line['text'] for line in vertical_lines])
            if replaced is None:
                unpaired_pages.append(page)
            else:
                quotes += replaced
                for line, text in zip(vertical_lines, texts):
                    line['text'] = text
        for line in lines:
            changed_lines += line.pop('original') != line['text']
            dumped.append(dict(line.pop('dump'), final=line['text']))
        page_results.append({'text_layer_lines': lines, 'img_width': info['img_width'],
                             'img_height': info['img_height']})
    if overrides:
        raise ValueError(f'overrides not found in OCR: {list(overrides)}')
    if corrections:
        raise ValueError(f'corrections for lines not in OCR or overridden: {list(corrections)[:5]}')
    if word_corrections:
        raise ValueError(f'word corrections for lines not in OCR or overridden: {list(word_corrections)[:5]}')
    if recognized:
        raise ValueError(f'recognized lines not found in OCR: {list(recognized)[:5]}')
    embed_text_layer_pdf(str(args.sanitized_pdf), str(args.output_pdf), page_results)
    if args.dump_lines:
        with args.dump_lines.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, ['page', 'line_id', 'vertical', 'box', 'raw', 'margin', 'rules',
                                             'judged', 'final'], delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(dumped)
    print(f'{args.output_pdf}: {count} pages, {changed_lines} lines normalized, '
          f'{removed_spaces} spaces removed')
    if args.word_corrections:
        print(f'{word_applied} word corrections applied from {args.word_corrections}')
    if args.corrections:
        print(f'{applied} corrections applied from {args.corrections}')
    if args.char_map:
        print(f'{mapped} characters mapped by {args.char_map}')
    if args.fix_brackets:
        print(f'{quotes} double quotes paired into 〝〟; quotes left unpaired on pages '
              f'{",".join(map(str, unpaired_pages)) or "none"}')
    for (variant, term), count in sorted(variants.items(), key=lambda item: -item[1]):
        print(f'{variant} -> {term}: {count}')


if __name__ == '__main__':
    main()
