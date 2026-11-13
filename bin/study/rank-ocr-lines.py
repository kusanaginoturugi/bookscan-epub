#!/usr/bin/env python3
"""Rank OCR lines for image review with a local text LLM.

The model score is only a review priority. It cannot establish OCR accuracy.
Book text stays in the ignored review directory and is sent to localhost only.
"""

import argparse
import csv
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pypdfium2
from pypdf import PdfReader

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # bin/, for text_layer_utils
from text_layer_utils import load_glossary


INTERNAL_GAP = re.compile(r'(?<=[一-龯ぁ-んァ-ヿ])[ \u3000]+(?=[一-龯ぁ-んァ-ヿ])')


def candidates(ocr_dir, stem, glossary=()):
    rows = []
    deleted_forms = [(term, term[:i] + term[i + 1:]) for term in glossary
                     for i in range(len(term)) if len(term) >= 4]
    for path in sorted(ocr_dir.glob(f'{stem}_*.json')):
        page = int(path.stem.rsplit('_', 1)[1])
        data = json.loads(path.read_text())
        xml_path = path.with_suffix('.xml')
        ruby_boxes = []
        if xml_path.exists():
            for block in ET.parse(xml_path).iter('BLOCK'):
                if block.get('TYPE') == 'ルビ':
                    x, y = int(block.get('X')), int(block.get('Y'))
                    ruby_boxes.append((x, y, x + int(block.get('WIDTH')),
                                       y + int(block.get('HEIGHT'))))
        for line in data['contents'][0]:
            raw = line.get('text', '')
            if not raw:
                continue
            confidence = float(line.get('confidence', 1))
            gaps = len(INTERNAL_GAP.findall(raw))
            box = line['boundingBox']
            x0, x1 = min(p[0] for p in box), max(p[0] for p in box)
            y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
            ruby_near = any(y < y1 and bottom > y0 and x < x1 + 22 and right > x0 - 22
                            for x, y, right, bottom in ruby_boxes)
            height = max(p[1] for p in box) - min(p[1] for p in box)
            density = height / max(len(raw.strip()), 1)
            flags = []
            if gaps:
                flags.append(f'和文中の空白{gaps}')
                if ruby_near:
                    flags.append('ルビ領域あり')
            if confidence < .9:
                flags.append('低信頼')
            if len(raw) >= 10 and density > 43:
                flags.append('字数不足候補')
            close_terms = sorted({term for term, form in deleted_forms
                                  if term not in raw and form in raw and len(form) >= 3})
            if close_terms:
                flags.append('辞書に近い表記: ' + '、'.join(close_terms[:3]))
            if not flags:
                continue
            rows.append({'page': page, 'line_id': line['id'], 'text': raw,
                         'confidence': confidence, 'flags': flags, 'box': box,
                         'prefilter': gaps * 4 + (ruby_near and gaps > 0) * 4 + (confidence < .9) * 3 +
                         (density > 43) + bool(close_terms) * 5})
    return sorted(rows, key=lambda row: (-row['prefilter'], row['page'], row['line_id']))


def score_batch(rows, url, model, glossary):
    numbered = '\n'.join(f'{i}: {row["text"]}' for i, row in enumerate(rows))
    terms = '、'.join(term for term in glossary if any(term in row['text'].replace(' ', '')
                                            for row in rows))
    prompt = ('次は縦書き小説からOCRした各1行です。画像は見られません。'
              '各行について、文章のつながりからOCR誤りが疑われる度合いを0〜5で採点してください。'
              '0は違和感なし、5は強い違和感。行末の文の途中や固有名詞、意図的な特殊組版は誤りと断定しない。'
              '空白が不自然ならそれを指摘する。入力の番号と同じ番号を全件返す。'
              'JSONのみ: {"scores":[{"id":0,"score":0,"reason":"短い理由"}]}\n'
              + (f'原本で確認済みの表記: {terms}\n' if terms else '') + numbered)
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
               'temperature': 0, 'max_tokens': max(220, 80 * len(rows)),
               'response_format': {'type': 'json_object'}}
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
    content = result['choices'][0]['message']['content']
    parsed = json.loads(content)
    scores = {int(item['id']): item for item in parsed['scores']}
    if set(scores) != set(range(len(rows))):
        raise ValueError(f'model returned incomplete ids: {list(scores)}')
    return [{'score': max(0, min(5, int(scores[i]['score']))),
             'reason': str(scores[i].get('reason', ''))} for i in range(len(rows))]


def write_review(pdf_path, rows, output):
    output.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(pdf_path)
    page_text = {}
    for row in rows:
        page = row['page']
        if page not in page_text:
            page_text[page] = reader.pages[page - 1].extract_text().splitlines()
        line_id = row['line_id']
        if line_id >= len(page_text[page]):
            raise ValueError(f'PDF line missing: page {page}, line {line_id}')
        row['pdf_text'] = page_text[page][line_id]
    with (output / 'ranked.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(['page', 'line_id', 'model_score', 'confidence', 'flags', 'reason',
                         'ocr_text', 'pdf_text'])
        for row in rows:
            writer.writerow([row['page'], row['line_id'], row['model_score'],
                             row['confidence'], ','.join(row['flags']), row['reason'],
                             row['text'], row['pdf_text']])
    pdf = pypdfium2.PdfDocument(str(pdf_path))
    cards = []
    for row in rows[:40]:
        page = row['page']
        line_id = row['line_id']
        image = pdf[page - 1].render(scale=300 / 72).to_pil().convert('RGB')
        box = row['box']
        x0, x1 = min(p[0] for p in box), max(p[0] for p in box)
        y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
        crop = image.crop((max(0, x0 - 25), max(0, y0 - 12),
                           min(image.width, x1 + 12), min(image.height, y1 + 12)))
        name = f'{page:03}-{line_id}.jpg'
        crop.save(output / name, quality=92)
        cards.append(f'<section><h2>{page}ページ・行{line_id} / スコア{row["model_score"]}</h2>'
                     f'<p>{html.escape(", ".join(row["flags"]))} / {html.escape(row["reason"])}</p>'
                     f'<img src="{name}"><p>元OCR: <code>{html.escape(row["text"])}</code></p>'
                     f'<p>PDF文字層: <code>{html.escape(row["pdf_text"])}</code></p></section>')
    pdf.close()
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>OCR文章レビュー</title>'
                                       '<style>body{font:16px sans-serif;max-width:900px;margin:2em}'
                                       'section{border-top:1px solid #ccc;padding:1em 0}'
                                       'img{max-width:100%}code{white-space:pre-wrap}</style>'
                                       '<h1>OCR文章レビュー候補</h1><p>スコアは文章の違和感。正誤は画像で確認する。</p>'
                                       + ''.join(cards), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ocr-stem', default='tiger-sanitized')
    parser.add_argument('--url', default='http://127.0.0.1:18091/v1/chat/completions')
    parser.add_argument('--model', default='local')
    parser.add_argument('--limit', type=int, default=40)
    parser.add_argument('--batch-size', type=int, default=5)
    parser.add_argument('--scores-file', type=Path,
                        help='reuse scores from an earlier ranked.tsv without calling the model')
    parser.add_argument('--glossary', type=Path,
                        help='image-verified terms to include in the model prompt')
    args = parser.parse_args()
    glossary = load_glossary(args.glossary) if args.glossary else []
    selected = candidates(args.ocr_dir, args.ocr_stem, glossary)[:args.limit]
    if args.scores_file:
        with args.scores_file.open(newline='') as handle:
            old = {(int(item['page']), int(item['line_id'])): item
                   for item in csv.DictReader(handle, delimiter='\t')}
        for row in selected:
            key = (row['page'], row['line_id'])
            if key not in old or old[key]['ocr_text'] != row['text']:
                raise ValueError(f'score missing or OCR changed: {key}')
            row['model_score'] = int(old[key]['model_score'])
            row['reason'] = old[key]['reason']
    else:
        for start in range(0, len(selected), args.batch_size):
            batch = selected[start:start + args.batch_size]
            scores = score_batch(batch, args.url, args.model, glossary)
            for row, score in zip(batch, scores):
                row['model_score'] = score['score']
                row['reason'] = score['reason']
            print(f'scored {start + len(batch)}/{len(selected)}', flush=True)
    selected.sort(key=lambda row: (-row['model_score'], -row['prefilter'], row['page']))
    write_review(args.pdf, selected, args.output)
    print(f'review: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
