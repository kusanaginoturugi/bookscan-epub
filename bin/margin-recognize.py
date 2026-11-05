#!/usr/bin/env python
"""Rerun the NDLOCR-Lite recognizer for a whole book with extra line margins.

Run with ../bookscan/vendor/.venv-ndlocr/bin/python. Saved OCR XML supplies the
line boxes, so only text recognition runs again. Vertical line crops are padded
left and right with page background. The output TSV keeps the saved OCR and one
column per margin; book text belongs under work/ and is not committed.
"""

import argparse
import csv
import importlib.util
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pypdfium2


ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sensitivity = load_module('ocr_sensitivity', ROOT / 'bin/ocr-sensitivity.py')
study = sensitivity.study


def ruby_boxes(path):
    boxes = []
    for block in ET.parse(path).iter('BLOCK'):
        if block.get('TYPE') == 'ルビ':
            x, y = int(block.get('X')), int(block.get('Y'))
            boxes.append((x, y, x + int(block.get('WIDTH')), y + int(block.get('HEIGHT'))))
    return boxes


def recognize_book(args):
    if args.output.exists():
        raise FileExistsError(f'{args.output} exists; move it before rerunning')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    extras = [int(value) for value in args.extras.split(',')]
    models = sensitivity.recognizers()
    document = pypdfium2.PdfDocument(str(args.pdf))
    first, last = (int(value) for value in args.pages.split('-')) if args.pages else (1, len(document))
    replicated = total = 0
    with args.output.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['page', 'line_id', 'vertical', 'width', 'height', 'pred_char_cnt', 'ruby_near',
                         'saved'] + [f'margin{extra}' for extra in extras])
        for page in range(first, last + 1):
            started = time.time()
            image = np.array(next(iter(document.render(
                pypdfium2.PdfBitmap.to_pil, page_indices=[page - 1],
                scale=sensitivity.RENDER_DPI / 72))).convert('RGB'))
            xml = args.ocr_dir / f'{args.ocr_stem}_{page:05}.xml'
            lines = sensitivity.load_lines(xml)
            rubies = ruby_boxes(xml)
            crops = []
            for line_id, line in enumerate(lines):
                tight = sensitivity.crop(image, line['x'], line['y'], line['w'], line['h'])
                for extra in extras:
                    padded = (sensitivity.pad_to_width(tight, tight.shape[1] + extra)
                              if line['h'] > line['w'] else tight)
                    crops.append(((line_id, extra), padded, line['count']))
            texts = sensitivity.recognize(models, crops) if crops else {}
            for line_id, line in enumerate(lines):
                x0, y0, x1, y1 = line['x'], line['y'], line['x'] + line['w'], line['y'] + line['h']
                near = any(y < y1 and bottom > y0 and x < x1 + 22 and right > x0 - 22
                           for x, y, right, bottom in rubies)
                outputs = [texts[(line_id, extra)] for extra in extras]
                if 0 in extras:
                    total += 1
                    replicated += texts[(line_id, 0)] == line['text']
                writer.writerow([page, line_id, int(line['h'] > line['w']), line['w'], line['h'],
                                 line['count'], int(near), line['text']] + outputs)
            handle.flush()
            print(f'page {page}: {len(lines)} lines, {time.time() - started:.1f}s', flush=True)
    if total:
        print(f'margin 0 reproduces saved OCR on {replicated}/{total} lines')


def score(args):
    # Gold TSV columns: page, line_id, expected. Spaces are ignored as in
    # bin/deskew-study.py.
    with args.output.open(newline='') as handle:
        rows = {(int(row['page']), int(row['line_id'])): row
                for row in csv.DictReader(handle, delimiter='\t')}
    columns = [name for name in next(iter(rows.values())) if name.startswith('margin')]
    gold = {}
    for path in args.gold:
        with path.open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                gold[(int(row['page']), int(row['line_id']))] = study.normalized(row['expected'])
    missing = [key for key in gold if key not in rows]
    if missing:
        raise ValueError(f'gold lines not in output: {missing}')
    chars = sum(len(text) for text in gold.values())
    print(f'{len(gold)} gold lines, {chars} chars')
    for column in ['saved'] + columns:
        for label, keys in (('all', list(gold)),
                            ('ruby_near', [key for key in gold if rows[key]['ruby_near'] == '1']),
                            ('no_ruby', [key for key in gold if rows[key]['ruby_near'] == '0'])):
            edits = sum(study.edit_distance(gold[key], study.normalized(rows[key][column])) for key in keys)
            wrong = sum(study.edit_distance(gold[key], study.normalized(rows[key][column])) > 0 for key in keys)
            print(f'{column:9} {label:9} lines {len(keys):3}  edits {edits:4}  wrong lines {wrong}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='TSV written by recognition and read by --score')
    parser.add_argument('--pdf', type=Path, help='PDF that was given to ocr.py')
    parser.add_argument('--ocr-dir', type=Path)
    parser.add_argument('--ocr-stem')
    parser.add_argument('--extras', default='0,8,14,20,30', help='background margin in pixels')
    parser.add_argument('--pages', help='inclusive range such as 1-20')
    parser.add_argument('--score', type=Path, nargs='+', dest='gold',
                        help='score an existing output TSV against gold TSV files')
    args = parser.parse_args()
    if args.gold:
        score(args)
    else:
        if not (args.pdf and args.ocr_dir and args.ocr_stem):
            parser.error('--pdf, --ocr-dir and --ocr-stem are required for recognition')
        recognize_book(args)


if __name__ == '__main__':
    main()
