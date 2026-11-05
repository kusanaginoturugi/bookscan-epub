#!/usr/bin/env python3
"""Prepare and inspect a small before/after deskew OCR experiment.

Run with ../bookscan/vendor/.venv-ndlocr/bin/python. Generated material stays
under work/deskew-study and is not committed.
"""

import argparse
import csv
import html
import importlib.util
import io
import json
import math
import statistics
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT.parent / 'bookscan/pdf/0101-20150604103726242_check.pdf'
PAGES = (35, 160, 179, 192, 209, 210)


def load_preparer():
    path = ROOT / 'bin/prepare-trial-pdf.py'
    spec = importlib.util.spec_from_file_location('prepare_trial_pdf', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(output):
    if any((output / name).exists() for name in ('rotated.pdf', 'original.pdf', 'pages.tsv')):
        raise FileExistsError('study outputs exist; move or remove them before preparing again')
    output.mkdir(parents=True, exist_ok=True)
    images = output / 'images'
    images.mkdir(exist_ok=True)
    source = PdfReader(SOURCE)
    original, rotated = PdfWriter(), PdfWriter()
    prep = load_preparer()
    rows = []
    for index, number in enumerate(PAGES, 1):
        page = source.pages[number - 1]
        if len(page.images) != 1:
            raise ValueError(f'page {number}: expected one image')
        image = Image.open(io.BytesIO(page.images[0].data)).convert('RGB')
        sample, text_page, _, _ = prep.classify(image)
        angle = prep.skew_angle(sample)
        if not text_page or not angle:
            raise ValueError(f'page {number}: no deskew candidate')
        original.add_page(page)
        target = rotated.add_page(page)
        changed = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
        target.images[0].replace(changed, quality=90, subsampling=0)
        image.save(images / f'{number:03}-original.jpg', quality=95)
        changed.save(images / f'{number:03}-rotated.jpg', quality=95)
        rows.append({'study_page': index, 'source_page': number, 'angle_deg': angle,
                     'width_px': image.width, 'height_px': image.height})
    with (output / 'original.pdf').open('wb') as handle:
        original.write(handle)
    with (output / 'rotated.pdf').open('wb') as handle:
        rotated.write(handle)
    with (output / 'pages.tsv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0], delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(rows)} pages prepared: {output}')
    for row in rows:
        print(f'{row["source_page"]}: {row["angle_deg"]:+.2f}°')


def reencode_control(output):
    """Re-encode the original page images with the deskew trial's JPEG settings."""
    target = output / 'reencoded.pdf'
    if target.exists():
        raise FileExistsError(target)
    source = PdfReader(output / 'original.pdf')
    writer = PdfWriter()
    for page in source.pages:
        result = writer.add_page(page)
        image = Image.open(io.BytesIO(result.images[0].data)).convert('RGB')
        result.images[0].replace(image, quality=90, subsampling=0)
    with target.open('wb') as handle:
        writer.write(handle)
    print(f'{target}: {len(writer.pages)} pages')


def embed(output, visible=False):
    """Rotate original OCR glyph positions with the page image."""
    sys.path.insert(0, str((ROOT.parent / 'bookscan/vendor/ndlocr-lite/src').resolve()))
    from ocr import _draw_text_layer_line
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5', isVertical=False))
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3', isVertical=True))

    with (output / 'pages.tsv').open(newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    source = PdfReader(output / 'rotated.pdf')
    if len(rows) != len(source.pages):
        raise ValueError('mapping and page count differ')
    buffer = io.BytesIO()
    overlay = canvas.Canvas(buffer, pagesize=(1, 1))
    for row, page in zip(rows, source.pages):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        overlay.setPageSize((width, height))
        overlay.saveState()
        overlay.translate(width / 2, height / 2)
        overlay.rotate(float(row['angle_deg']))
        overlay.translate(-width / 2, -height / 2)
        number = int(row['source_page'])
        ocr_path = ROOT / f'work/trial/infinity-upper-ocr/0101-20150604103726242_check_{number:05}.json'
        data = json.loads(ocr_path.read_text())
        for item in data['contents'][0]:
            box = item['boundingBox']
            xs, ys = [point[0] for point in box], [point[1] for point in box]
            line = {'x': min(xs), 'y': min(ys), 'width': max(xs) - min(xs),
                    'height': max(ys) - min(ys), 'text': item['text'],
                    'is_vertical': item['isVertical'] == 'true'}
            _draw_text_layer_line(overlay, line, data['imginfo']['img_width'],
                                  data['imginfo']['img_height'], width, height,
                                  visible=visible)
        overlay.restoreState()
        overlay.showPage()
    overlay.save()
    buffer.seek(0)
    text_pages = PdfReader(buffer)
    writer = PdfWriter()
    for page, layer in zip(source.pages, text_pages.pages):
        result = writer.add_page(page)
        result.merge_page(layer)
    target = output / ('transformed-visible.pdf' if visible else 'transformed-ocr.pdf')
    with target.open('wb') as handle:
        writer.write(handle)
    result = PdfReader(target)
    if len(result.pages) != len(rows):
        raise ValueError('output page count differs')
    for index, page in enumerate(result.pages):
        if not page.extract_text() or len(page.images) != len(source.pages[index].images):
            raise ValueError(f'page {index + 1}: missing text or image')
    print(f'{target}: {len(result.pages)} pages')


def normalized(text):
    return ''.join(unicodedata.normalize('NFC', text).split())


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def score(output):
    with (output / 'pages.tsv').open(newline='') as handle:
        pages = list(csv.DictReader(handle, delimiter='\t'))
    with (output / 'gold.tsv').open(newline='') as handle:
        gold = list(csv.DictReader(handle, delimiter='\t'))
    lookup = {}
    reencoded_lookup = {}
    paired_lines = changed_lines = 0
    subset_matches = repeated_matches = 0
    reencoded_changes = 0
    ruby_regions = 0
    votes = {'all_same': 0, 'original_reencoded': 0,
             'original_rotated': 0, 'reencoded_rotated': 0, 'all_different': 0}
    distances = []
    for row in pages:
        source_page, study_page = int(row['source_page']), int(row['study_page'])
        before = json.loads((ROOT / f'work/trial/infinity-upper-ocr/'
                             f'0101-20150604103726242_check_{source_page:05}.json').read_text())
        after = json.loads((output / f'rotated-ocr/rotated_{study_page:05}.json').read_text())
        a = {int(item['id']): item for item in before['contents'][0]}
        b = {int(item['id']): item for item in after['contents'][0]}
        control_paths = {
            'subset': output / f'original-ocr-control/original_{study_page:05}.json',
            'repeat': output / f'rotated-ocr-repeat/rotated_{study_page:05}.json',
            'reencoded': output / f'reencoded-ocr/reencoded_{study_page:05}.json',
        }
        controls = {name: {int(item['id']): item for item in
                           json.loads(path.read_text())['contents'][0]}
                    for name, path in control_paths.items()}
        if a.keys() != b.keys():
            raise ValueError(f'line ids changed on page {source_page}')
        if any(a.keys() != items.keys() for items in controls.values()):
            raise ValueError(f'control line ids changed on page {source_page}')
        lookup[source_page] = (a, b)
        reencoded_lookup[source_page] = controls['reencoded']
        paired_lines += len(a)
        for path in (ROOT / f'work/trial/infinity-upper-ocr/'
                     f'0101-20150604103726242_check_{source_page:05}.xml',
                     output / f'rotated-ocr/rotated_{study_page:05}.xml'):
            ruby_regions += sum(block.get('TYPE') == 'ルビ'
                                for block in ET.parse(path).iter('BLOCK'))
        angle = math.radians(float(row['angle_deg']))
        width, height = before['imginfo']['img_width'], before['imginfo']['img_height']
        for line_id in a:
            first, second = a[line_id], b[line_id]
            subset_matches += first['text'] == controls['subset'][line_id]['text']
            repeated_matches += second['text'] == controls['repeat'][line_id]['text']
            reencoded = controls['reencoded'][line_id]['text']
            reencoded_changes += normalized(first['text']) != normalized(reencoded)
            x, y, z = [normalized(text) for text in (first['text'], reencoded, second['text'])]
            vote = ('all_same' if x == y == z else
                    'original_reencoded' if x == y else
                    'original_rotated' if x == z else
                    'reencoded_rotated' if y == z else 'all_different')
            votes[vote] += 1
            if normalized(first['text']) != normalized(second['text']):
                changed_lines += 1
                continue
            ox = sum(p[0] for p in first['boundingBox']) / 4
            oy = sum(p[1] for p in first['boundingBox']) / 4
            tx = width / 2 + math.cos(angle) * (ox - width / 2) + math.sin(angle) * (oy - height / 2)
            ty = height / 2 - math.sin(angle) * (ox - width / 2) + math.cos(angle) * (oy - height / 2)
            bx = sum(p[0] for p in second['boundingBox']) / 4
            by = sum(p[1] for p in second['boundingBox']) / 4
            distances.append(math.hypot(tx - bx, ty - by))
    scores = []
    for row in gold:
        page, line_id = int(row['source_page']), int(row['line_id'])
        before, after = lookup[page]
        reference = normalized(row['expected'])
        first, second = normalized(before[line_id]['text']), normalized(after[line_id]['text'])
        reencoded = normalized(reencoded_lookup[page][line_id]['text'])
        majority = (first if first == reencoded or first == second else
                    reencoded if reencoded == second else first)
        scores.append({'page': page, 'line_id': line_id, 'reference_chars': len(reference),
                       'before_edits': edit_distance(reference, first),
                       'reencoded_edits': edit_distance(reference, reencoded),
                       'after_edits': edit_distance(reference, second),
                       'line_majority_edits': edit_distance(reference, majority),
                       'expected': row['expected'], 'before': before[line_id]['text'],
                       'reencoded': reencoded_lookup[page][line_id]['text'],
                       'after': after[line_id]['text']})
    chars = sum(item['reference_chars'] for item in scores)
    before_edits = sum(item['before_edits'] for item in scores)
    reencoded_edits = sum(item['reencoded_edits'] for item in scores)
    after_edits = sum(item['after_edits'] for item in scores)
    majority_edits = sum(item['line_majority_edits'] for item in scores)
    report = {'source_pages': [int(row['source_page']) for row in pages],
              'paired_lines': paired_lines, 'changed_lines_ignoring_whitespace': changed_lines,
              'position_check_lines': len(distances),
              'position_center_error_median_px': statistics.median(distances),
              'position_center_error_max_px': max(distances),
              'same_input_control_matching_lines': subset_matches,
              'rotated_repeat_matching_lines': repeated_matches,
              'reencoded_changed_lines': reencoded_changes,
              'three_way_agreement': votes, 'ruby_regions_in_original_and_rotated_xml': ruby_regions,
              'gold_lines': len(scores), 'gold_chars': chars,
              'before_edits': before_edits, 'reencoded_edits': reencoded_edits,
              'after_edits': after_edits, 'line_majority_edits': majority_edits,
              'before_cer': before_edits / chars,
              'reencoded_cer': reencoded_edits / chars, 'after_cer': after_edits / chars,
              'scores': scores}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    cards = []
    for item in scores:
        page, line_id = item['page'], item['line_id']
        crop = f'gold-crops/{page}-{line_id}.jpg'
        cards.append(f'<section><h2>{page}ページ・行{line_id}: '
                     f'{item["before_edits"]} → {item["reencoded_edits"]} → '
                     f'{item["after_edits"]} 編集差</h2>'
                     f'<img src="{crop}"><p>正解: {html.escape(item["expected"])}</p>'
                     f'<p>補正前: {html.escape(item["before"])}</p>'
                     f'<p>再保存のみ: {html.escape(item["reencoded"])}</p>'
                     f'<p>補正後: {html.escape(item["after"])}</p></section>')
    (output / 'index.html').write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><title>傾き補正とOCRの比較</title>'
        '<style>body{font:16px sans-serif;max-width:900px;margin:2em}'
        'section{border-top:1px solid #aaa;padding:1em 0}img{max-width:100%}</style>'
        '<h1>傾き補正とOCRの比較</h1>'
        f'<p>6ページ・{len(scores)}行・{chars}文字。空白と改行を除く編集差: '
        f'原本 {before_edits} ({before_edits/chars:.1%})、'
        f'再保存のみ {reencoded_edits} ({reencoded_edits/chars:.1%})、'
        f'傾き補正後 {after_edits} ({after_edits/chars:.1%})、'
        f'行単位多数決 {majority_edits} ({majority_edits/chars:.1%})。</p>'
        '<p>差が出た行を優先した小標本なので、本全体の精度差を推定する値ではない。</p>'
        f'<p>同じ入力の再実行は{subset_matches}/{paired_lines}行と'
        f'{repeated_matches}/{paired_lines}行で完全一致。再保存のみで変化したのは'
        f'{reencoded_changes}行。OCRのXMLで検出したルビ領域は両条件で計{ruby_regions}件。</p>'
        f'<p>文字位置: 同一OCR文字列の{len(distances)}行について、回転した元OCR枠の中心と'
        f'補正後OCR枠の中心の差は中央値{statistics.median(distances):.2f}px、'
        f'最大{max(distances):.2f}px。</p>'
        '<p><a href="transformed-ocr.pdf">回転後の検索可能PDF</a> / '
        '<a href="transformed-visible.pdf">文字層の青字表示</a> / '
        '<a href="search-hit-35.jpg">35ページの検索位置</a></p>'
        + ''.join(cards) + '</html>', encoding='utf-8')
    print(f'{output / "index.html"}: {len(scores)} lines, {chars} chars, '
          f'original {before_edits}, reencoded {reencoded_edits}, rotated {after_edits}, '
          f'line majority {majority_edits} edits')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=Path('work/deskew-study'))
    parser.add_argument('--embed', action='store_true', help='add rotated original OCR to the deskewed PDF')
    parser.add_argument('--visible', action='store_true', help='draw blue text for position inspection')
    parser.add_argument('--score', action='store_true', help='score image-transcribed lines and positions')
    parser.add_argument('--reencode-control', action='store_true',
                        help='build a no-rotation PDF with the same JPEG re-encoding')
    args = parser.parse_args()
    if args.embed:
        embed(args.output, visible=args.visible)
    elif args.score:
        score(args.output)
    elif args.reencode_control:
        reencode_control(args.output)
    else:
        prepare(args.output)


if __name__ == '__main__':
    main()
