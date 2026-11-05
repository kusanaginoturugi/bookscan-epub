#!/usr/bin/env python3
"""Create a reproducible OCR review sample and score image-verified text.

Run with ../bookscan/vendor/.venv-ndlocr/bin/python. Gold TSV columns are
page, line_id, expected. Keep book text in work/ rather than in Git.
"""

import argparse
import csv
import html
import json
import random
import unicodedata
from pathlib import Path

import pypdfium2
from pypdf import PdfReader

from text_layer_utils import clean_text, load_glossary


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


def page_lines(path):
    data = json.loads(path.read_text())
    return data['contents'][0], data['imginfo']


def choose_line(lines):
    candidates = [line for line in lines if len(line.get('text', '')) >= 15]
    return max(candidates, key=lambda line: len(line['text'])) if candidates else None


def render_review(pdf_path, samples, output):
    pdf = pypdfium2.PdfDocument(str(pdf_path))
    pages_dir = output / 'pages'
    crops_dir = output / 'crops'
    pages_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        number = sample['page']
        image = pdf[number - 1].render(scale=300 / 72).to_pil().convert('RGB')
        page_path = pages_dir / f'{number:03}.jpg'
        image.save(page_path, quality=85)
        line = sample['line']
        if line is None:
            continue
        box = line['boundingBox']
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        width, height = image.size
        pad = 12
        crop = image.crop((max(0, min(xs) - pad), max(0, min(ys) - pad),
                           min(width, max(xs) + pad), min(height, max(ys) + pad)))
        crop.save(crops_dir / f'{number:03}-{line["id"]}.jpg', quality=95)
    pdf.close()


def make_html(samples, empty_pages, output, result, gold_path):
    cards = []
    for sample in samples:
        number = sample['page']
        line = sample['line']
        page_link = f'pages/{number:03}.jpg'
        if line:
            crop_link = f'crops/{number:03}-{line["id"]}.jpg'
            content = (f'<img class="crop" src="{crop_link}" alt="page {number} line {line["id"]}">'
                       f'<p>line_id={line["id"]} / 元OCR: <code>{html.escape(line["text"])}</code></p>'
                       f'<p>PDF文字層: <code>{html.escape(sample["pdf_line"])}</code></p>')
        else:
            content = '<p>長いOCR行なし。ページ画像で確認。</p>'
        full_text = html.escape(sample['page_text'])
        cards.append(f'<section><h2>{number}ページ（{sample["kind"]}、抽出{sample["chars"]}文字）</h2>'
                     f'<a href="{page_link}" target="_blank">ページ全体を開く</a>'
                     f'<img class="page" src="{page_link}" alt="page {number}">{content}'
                     f'<details><summary>ページ全体の抽出文字</summary><pre>{full_text}</pre></details></section>')
    body = '\n'.join(cards)
    gold = result['gold']
    if gold:
        ordinary = gold['groups']['ordinary']
        special = gold['groups']['special']
        summary = (f'<p>通常行: {ordinary["edits"]}/{ordinary["reference_chars"]} 編集差。'
                   f'例外行: {special["edits"]}/{special["reference_chars"]} 編集差。'
                   'これは選んだ行のCERで、未検出の文字は含まない。</p>')
    else:
        summary = '<p>正解テキスト未入力。</p>'
    queries = result['manual_search_queries']
    if queries:
        misses = ', '.join(f'{item["page"]}ページ「{html.escape(item["term"])}」'
                           for item in queries['details'] if not item['found'])
        summary += (f'<p>画像で確認した語句の文字層照合: {queries["found"]}/{queries["total"]}。'
                    f'未検出: {misses}</p>')
    summary += (f'<p>低信頼OCRのあるページ: {", ".join(map(str, result["low_confidence_pages"]))}。'
                f'本文中で抽出が100文字未満のページ: '
                f'{", ".join(map(str, result["short_core_pages"]))}。</p>')
    gold_hint = html.escape(str(gold_path)) if gold_path else '指定した正解TSV'
    (output / 'index.html').write_text('''<!doctype html><html lang="ja"><meta charset="utf-8">
<title>検索可能PDFのOCR評価</title><style>body{font:16px sans-serif;margin:2em;max-width:1100px}
section{border-top:1px solid #bbb;padding:1em 0}img.page{display:block;max-width:420px;max-height:600px}
img.crop{display:block;max-width:100%;margin-top:1em;border:1px solid #bbb}code{white-space:pre-wrap}
</style><h1>OCR評価レビュー</h1><p>正解は画像で確認し、
''' + gold_hint + ''' の expected 列へ記入する。全文画像はリンクから確認。</p>''' +
summary + '<p>抽出が空のページ: ' + ', '.join(map(str, empty_pages)) + '</p>' +
body + '</html>', encoding='utf-8')


def score_gold(gold_path, lookup, reader, special_pages):
    if not gold_path.exists():
        return None
    with gold_path.open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle, delimiter='\t') if row.get('expected')]
    chars = errors = 0
    searches = hits = 0
    groups = {'ordinary': {'lines': 0, 'reference_chars': 0, 'edits': 0},
              'special': {'lines': 0, 'reference_chars': 0, 'edits': 0}}
    scored = []
    for row in rows:
        page = int(row['page'])
        line_id = int(row['line_id'])
        line = next((item for item in lookup[page] if item['id'] == line_id), None)
        if line is None:
            raise ValueError(f'page {page}: line_id {line_id} not found')
        expected = normalized(row['expected'])
        actual = normalized(line['text'])
        distance = edit_distance(expected, actual)
        chars += len(expected)
        errors += distance
        group = groups['special' if page in special_pages else 'ordinary']
        group['lines'] += 1
        group['reference_chars'] += len(expected)
        group['edits'] += distance
        # Image-verified substrings test whether useful terms survive in the
        # final PDF text layer. This is not a PDF viewer's interactive search.
        page_text = ''.join(reader.pages[page - 1].extract_text().split())
        words = [''.join(expected.split())[i:i + 6]
                 for i in (0, max(0, len(expected) // 2 - 3))
                 if len(''.join(expected.split())[i:i + 6]) == 6]
        searches += len(words)
        hits += sum(word in page_text for word in words)
        scored.append({'page': page, 'line_id': line_id, 'kind': 'special' if page in special_pages else 'ordinary',
                       'reference_chars': len(expected),
                       'edits': distance, 'searches': len(words),
                       'search_hits': sum(word in page_text for word in words)})
    for group in groups.values():
        group['cer'] = (group['edits'] / group['reference_chars']
                        if group['reference_chars'] else None)
    return {'lines': len(scored), 'reference_chars': chars, 'edits': errors,
            'cer': errors / chars if chars else None, 'search_hits': hits,
            'searches': searches, 'groups': groups, 'details': scored}


def score_queries(path, reader):
    if not path or not path.exists():
        return None
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    details = []
    for row in rows:
        page = int(row['page'])
        term = normalized(row['term'])
        if not 1 <= page <= len(reader.pages) or not term:
            raise ValueError(f'invalid search query: {row}')
        found = term in normalized(reader.pages[page - 1].extract_text())
        details.append({'page': page, 'term': term, 'found': found})
    return {'found': sum(item['found'] for item in details),
            'total': len(details), 'details': details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ocr-stem', default='tiger-sanitized')
    parser.add_argument('--gold', type=Path)
    parser.add_argument('--queries', type=Path)
    parser.add_argument('--normalized-text-layer', action='store_true',
                        help='compare PDF text with whitespace-normalized OCR JSON')
    parser.add_argument('--overrides', type=Path,
                        help='image-verified corrections used for the PDF text layer')
    parser.add_argument('--glossary', type=Path,
                        help='verified terms joined within the PDF text layer')
    parser.add_argument('--special-pages', default='155,192,257,311,316,321')
    parser.add_argument('--seed', type=int, default=20261104)
    args = parser.parse_args()
    reader = PdfReader(args.pdf)
    glossary = load_glossary(args.glossary) if args.glossary else []
    overrides = {}
    if args.overrides:
        with args.overrides.open(newline='') as handle:
            for row in csv.DictReader(handle, delimiter='\t'):
                key = (int(row['page']), int(row['line_id']))
                if key in overrides:
                    raise ValueError(f'duplicate override: {key}')
                overrides[key] = row['corrected_text']
    lookup = {}
    counts = {}
    low_confidence_pages = []
    text_mismatches = []
    for number in range(1, len(reader.pages) + 1):
        path = args.ocr_dir / f'{args.ocr_stem}_{number:05}.json'
        lines, _ = page_lines(path)
        lookup[number] = lines
        extracted = reader.pages[number - 1].extract_text()
        counts[number] = len(extracted)
        expected = ('\n'.join(overrides.get((number, int(line['id'])),
                                             clean_text(line['text'], line['isVertical'] == 'true', glossary))
                               for line in lines)
                    if args.normalized_text_layer else
                    (args.ocr_dir / f'{args.ocr_stem}_{number:05}.txt').read_text())
        if extracted != expected:
            text_mismatches.append(number)
        if any(float(line.get('confidence', 1)) < .5 for line in lines if line.get('text')):
            low_confidence_pages.append(number)
    special = [int(x) for x in args.special_pages.split(',') if x.strip()]
    if any(page < 1 or page > len(reader.pages) for page in special):
        parser.error('special page out of range')
    eligible = [page for page in range(11, len(reader.pages) - 20)
                if 350 <= counts[page] <= 850 and page not in special]
    if len(eligible) < 10:
        raise ValueError('fewer than 10 ordinary pages')
    regular = sorted(random.Random(args.seed).sample(eligible, 10))
    empty = [page for page, count in counts.items() if count == 0]
    samples = []
    for page in sorted(set(regular + special + empty)):
        line = choose_line(lookup[page])
        page_text = reader.pages[page - 1].extract_text()
        pdf_lines = page_text.splitlines()
        samples.append({'page': page,
                        'kind': '通常' if page in regular else ('抽出なし' if page in empty else '例外'),
                        'chars': counts[page], 'line': line,
                        'pdf_line': pdf_lines[line['id']] if line and line['id'] < len(pdf_lines) else '',
                        'page_text': page_text})
    args.output.mkdir(parents=True, exist_ok=True)
    render_review(args.pdf, samples, args.output)
    with (args.output / 'sample.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(['page', 'kind', 'line_id', 'ocr_chars', 'expected'])
        for sample in samples:
            writer.writerow([sample['page'], sample['kind'],
                             sample['line']['id'] if sample['line'] else '', sample['chars'], ''])
    result = {'pdf': str(args.pdf), 'pages': len(reader.pages), 'nonempty': len(reader.pages) - len(empty),
              'empty_pages': empty, 'sample_pages': regular, 'special_pages': special,
              'short_core_pages': [page for page in range(11, len(reader.pages) - 20)
                                   if counts[page] < 100],
              'low_confidence_pages': low_confidence_pages,
              'pdf_ocr_text_mismatch_pages': text_mismatches,
              'gold': score_gold(args.gold, lookup, reader, set(special)) if args.gold else None,
              'manual_search_queries': score_queries(args.queries, reader)}
    make_html(samples, empty, args.output, result, args.gold)
    (args.output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'review: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
