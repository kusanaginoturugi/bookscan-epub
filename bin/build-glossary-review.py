#!/usr/bin/env python3
"""Show glossary candidates next to their first OCR image crop."""

import argparse
import csv
import html
import json
from pathlib import Path

import pypdfium2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('candidates', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--ocr-stem', required=True)
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    with args.candidates.open(newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    pdf = pypdfium2.PdfDocument(str(args.pdf))
    cards = []
    for row in rows:
        term = row['term']
        match = None
        for page in map(int, row['pages'].split(',')):
            data = json.loads((args.ocr_dir / f'{args.ocr_stem}_{page:05}.json').read_text())
            match = next((line for line in data['contents'][0] if term in line['text']), None)
            if match:
                break
        if match is None:
            continue
        image = pdf[page - 1].render(scale=300 / 72).to_pil().convert('RGB')
        box = match['boundingBox']
        x0, x1 = min(p[0] for p in box), max(p[0] for p in box)
        y0, y1 = min(p[1] for p in box), max(p[1] for p in box)
        crop = image.crop((max(0, x0 - 25), max(0, y0 - 12),
                           min(image.width, x1 + 12), min(image.height, y1 + 12)))
        filename = f'{page:03}-{match["id"]}.jpg'
        crop.save(args.output / filename, quality=92)
        cards.append(f'<section><h2>{html.escape(term)} / {row["occurrences"]}件</h2>'
                     f'<p>{page}ページ・行{match["id"]}。候補は画像確認前。</p>'
                     f'<img src="{filename}"><p><code>{html.escape(match["text"])}</code></p></section>')
    pdf.close()
    (args.output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>辞書候補レビュー</title>'
        '<style>body{font:16px sans-serif;max-width:950px;margin:2em}'
        'section{border-top:1px solid #bbb;padding:1em 0}'
        'img{max-width:100%}code{white-space:pre-wrap}</style>'
        '<h1>辞書候補と原本画像</h1><p>頻度はOCR上の回数。画像で確認するまで辞書に採用しない。</p>'
        + ''.join(cards), encoding='utf-8')
    print(f'{len(cards)} candidate crops: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
