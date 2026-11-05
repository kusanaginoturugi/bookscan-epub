#!/usr/bin/env python3
"""Draft first-appearance reading notes with a local OpenAI-compatible LLM."""

import argparse
import csv
import json
import re
import time
import urllib.request
from pathlib import Path

from pypdf import PdfReader


PDFS = [
    '1793868466_夏への扉 - 注釈試作 薄色右開き 001-120.pdf',
    '1793868514_夏への扉 - 注釈試作 薄色右開き 121-240.pdf',
    '1793868531_夏への扉 - 注釈試作 薄色右開き 241-356.pdf',
]


def snippet(text, term):
    index = text.find(term)
    if index < 0:
        raise ValueError(f'{term!r} missing from its first page')
    return text[max(0, index - 75):index + len(term) + 75]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18089/v1/chat/completions')
    parser.add_argument('--batch-size', type=int, default=8)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    rows = list(csv.DictReader((root / 'data/summer-door-reading-term-candidates_fix.tsv').open(), delimiter='\t'))
    pages = [page.extract_text().replace('\n', '') for name in PDFS
             for page in PdfReader(root / 'output' / name).pages]
    if len(pages) != 356 or len(rows) != 234:
        raise ValueError('unexpected PDF or candidate count')

    raw = root / 'work/summer-door-glossary-llm.jsonl'
    done = {entry['batch'] for line in raw.read_text().splitlines()
            if (entry := json.loads(line))} if raw.exists() else set()
    batches = [rows[i:i + args.batch_size] for i in range(0, len(rows), args.batch_size)]
    for number, batch in enumerate(batches):
        if number in done:
            continue
        items = [{'id': index, 'term': row['term'], 'context': snippet(pages[int(row['first_pdf_page']) - 1], row['term'])}
                 for index, row in enumerate(batch)]
        prompt = (
            '小説の初出箇所に添える短い辞書説明を作って。'
            '各説明は日本語で1文、15〜40字。語そのものが何を指すかだけ述べる。'
            '「主人公」「物語」「この作品」「作中の背景」など小説との関係は書かない。'
            '歴史上の由来や作品の内容を推測しない。1956年より後の知識や先の展開は書かない。'
            '固有名詞の正体が確定しない場合は「本文に登場する名称。詳細は不明」と書く。'
            '例: コネチカット州→アメリカ合衆国北東部の州。'
            '例: H・G・ウエルズ→イギリスの作家。'
            '例: 架空の商品名→本文に登場する商品名。'
            'JSONオブジェクトのみ返す。'
            '形式: {"items":[{"id":0,"note":"説明"}]}。全idを一度ずつ含める。\n'
            + json.dumps(items, ensure_ascii=False)
        )
        body = json.dumps({
            'messages': [{'role': 'user', 'content': prompt}],
            'response_format': {'type': 'json_object'},
            'temperature': 0,
            'max_tokens': 700,
        }, ensure_ascii=False).encode()
        request = urllib.request.Request(args.url, body, {'Content-Type': 'application/json'})
        for attempt in range(3):
            try:
                response = json.load(urllib.request.urlopen(request, timeout=300))
                answer = json.loads(response['choices'][0]['message']['content'])
                if not isinstance(answer.get('items'), list):
                    raise ValueError('missing items')
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5)
        with raw.open('a') as stream:
            stream.write(json.dumps({'batch': number, 'items': answer['items']}, ensure_ascii=False) + '\n')
        print(f'{number + 1}/{len(batches)}: {len(answer["items"])} notes', flush=True)

    notes = {}
    for line in raw.read_text().splitlines():
        record = json.loads(line)
        batch = batches[record['batch']]
        for item in record['items']:
            index = item.get('id')
            if isinstance(index, int) and 0 <= index < len(batch):
                note = re.sub(r'\s+', ' ', str(item.get('note', ''))).strip()
                if note:
                    notes[batch[index]['term']] = note
    missing = [row['term'] for row in rows if row['term'] not in notes]
    if missing:
        raise ValueError(f'missing {len(missing)} terms: {missing}')

    note_origins = {term: 'ローカルLLM' for term in notes}
    references = {}
    for line in (root / 'data/summer-door-note-candidates.md').read_text().splitlines():
        if not re.match(r'^\|\s*\d+\s*\|', line):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        term = re.sub(r'（GM）$', '', cells[1])
        if term in notes:
            notes[term] = cells[2]
            note_origins[term] = '既存の出典付き説明'
            match = re.search(r'https?://[^)]+', cells[3])
            references[term] = match.group(0) if match else ''
    existing = json.loads((root / 'data/summer-door-reading-notes.json').read_text())
    for note in existing:
        for term in ('修正第四条', 'ホイスラー', '母の肖像'):
            if term in notes and (term == '修正第四条' and note['id'] == 'fourth-amendment'
                                  or term != '修正第四条' and note['id'] == 'whistlers-mother'):
                notes[term] = note['summary']
                note_origins[term] = '既存の出典付き説明'
                references[term] = note['source']

    for correction in csv.DictReader((root / 'data/summer-door-glossary-corrections.tsv').open(), delimiter='\t'):
        term = correction['term']
        if term not in notes:
            raise ValueError(f'correction term missing from glossary: {term}')
        notes[term] = correction['note']
        note_origins[term] = '本文・資料で補正'
        if correction['reference_url']:
            references[term] = correction['reference_url']

    output = root / 'data/summer-door-reading-glossary.tsv'
    with output.open('w', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['first_pdf_page', 'term', 'note', 'note_source', 'reference_url', 'review_status'])
        for row in rows:
            term = row['term']
            writer.writerow([row['first_pdf_page'], term, notes[term], note_origins[term],
                             references.get(term, ''), row['review_status']])
    print(f'{len(notes)} notes -> {output}')


if __name__ == '__main__':
    main()
