#!/usr/bin/env python
"""Score automatic judges of dakuten/handakuten spellings against a human glossary.

Run with .venv-ndl/bin/python. Candidate pairs and their occurrences come from
bin/build-voicing-review.py (same text rules). A pair settled in the human
glossary TSV (from the review page) gives the right spelling of every
occurrence of either form. Judges:

  ndl      the NDLOCR-Lite reading in the text layer (baseline)
  crop     NDLOCR-Lite recognizer run again on the word alone, cropped from the
           page at full resolution, so the marks are not shrunk with the line
  llm      a local LLM shown the passage (previous, current and next line) with
           each spelling, asked in both orders which reads as natural Japanese;
           an answer counts only when both orders agree (--llm-sample of the
           cases NDL read right are asked, and every case NDL read wrong)

usage: bin/study/check-voicing-judges.py PDF OCR_DIR GLOSSARY OUT.tsv --ocr-stem STEM
         [--recognized margin.tsv] [--yomitoku-dir DIR]
"""

import argparse
import csv
import importlib.util
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pypdfium2

ROOT = Path(__file__).resolve().parents[1]  # bin/, whose modules this script loads
sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = load_module('voicing_review', ROOT / 'build-voicing-review.py')
sensitivity = load_module('ocr_sensitivity', ROOT / 'ocr-sensitivity.py')
variant_llm = load_module('auto_variant_glossary', ROOT / 'auto-variant-glossary.py')
PROMPT = """次のA・Bは、小説をOCRで読み取った同じ箇所です。カタカナ語1語の濁点・半濁点だけが違います。
OCRは「バ」と「パ」のような濁点・半濁点をよく読み違えます。日本語の文章として自然で正しい方を、AかBの1文字だけで答えてください。

A: {a}

B: {b}"""


def truth_table(path):
    """{(a, b) sorted pair: right spelling} from a review glossary TSV."""
    truth = {}
    with path.open(newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            if row['verified'] != 'yes':
                continue
            for variant in filter(None, row['variants'].split(' ')):
                other = variant.split('(')[0]
                truth[tuple(sorted((row['term'], other)))] = row['term']
    return truth


def word_box(line, start, end, margin):
    x0, y0, x1, y1 = line['box']
    size = max(len(line['text']), 1)
    if line['vertical']:
        per = (y1 - y0) / size
        return (x0 - 4, y0 + (start - margin) * per, x1 + 4, y0 + (end + margin) * per)
    per = (x1 - x0) / size
    return (x0 + (start - margin) * per, y0 - 4, x0 + (end + margin) * per, y1 + 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('glossary', type=Path, help='glossary TSV saved from the review page')
    parser.add_argument('output', type=Path, help='per-occurrence TSV')
    parser.add_argument('--ocr-stem', required=True)
    parser.add_argument('--recognized', type=Path)
    parser.add_argument('--column', default='margin30')
    parser.add_argument('--min-height', type=int, default=400)
    parser.add_argument('--yomitoku-dir', type=Path)
    parser.add_argument('--min-length', type=int, default=3)
    parser.add_argument('--margin', type=float, default=0.3, help='extra characters around the word crop')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--llm-sample', type=int, default=0,
                        help='cases NDL read right to ask the LLM (all wrong ones are asked when > 0)')
    parser.add_argument('--llm-url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--llm-model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    args = parser.parse_args()

    lines = review.load_lines(args)
    yomitoku = review.load_yomitoku(args.yomitoku_dir) if args.yomitoku_dir else {}
    occurrences, pairs = review.find_pairs(lines, yomitoku, args.min_length)
    truth = truth_table(args.glossary)
    cases = []
    for key in pairs:
        if key not in truth:
            continue
        for word in key:
            for index, start, end in occurrences.get(word, []):
                cases.append(dict(pair=key, right=truth[key], read=word, index=index, start=start, end=end))

    models = sensitivity.recognizers(args.device)
    document = pypdfium2.PdfDocument(str(args.pdf))
    by_page = defaultdict(list)
    for case in cases:
        by_page[lines[case['index']]['page']].append(case)
    for page, items in sorted(by_page.items()):
        image = np.array(document[page - 1].render(scale=300 / 72).to_pil().convert('RGB'))
        crops = []
        for n, case in enumerate(items):
            line = lines[case['index']]
            x0, y0, x1, y1 = (int(v) for v in word_box(line, case['start'], case['end'], args.margin))
            crop = image[max(0, y0):y1, max(0, x0):x1]
            crops.append((n, crop, len(case['read'])))
        texts = sensitivity.recognize(models, crops)
        for n, case in enumerate(items):
            case['crop_text'] = texts[n]
    document.close()

    def crop_choice(case):
        a, b = case['pair']
        text = case['crop_text']
        hits = [w for w in (a, b) if w in text]
        return hits[0] if len(hits) == 1 else ''

    if args.llm_sample:
        right = [c for c in cases if c['read'] == c['right']]
        asked = [c for c in cases if c['read'] != c['right']]
        asked += random.Random(20261111).sample(right, min(args.llm_sample, len(right)))
        for case in asked:
            index, start, end = case['index'], case['start'], case['end']
            page = lines[index]['page']
            same_page = lambda i: 0 <= i < len(lines) and lines[i]['page'] == page
            before = lines[index - 1]['text'] if same_page(index - 1) else ''
            after = lines[index + 1]['text'] if same_page(index + 1) else ''
            text = lines[index]['text']
            passage = {w: before + text[:start] + w + text[end:] + after for w in case['pair']}
            a, b = case['pair']
            first = variant_llm.ask(args.llm_url, args.llm_model, PROMPT.format(a=passage[a], b=passage[b]))
            second = variant_llm.ask(args.llm_url, args.llm_model, PROMPT.format(a=passage[b], b=passage[a]))
            one = {'A': a, 'B': b}.get(first)
            two = {'A': b, 'B': a}.get(second)
            case['llm'] = one if one and one == two else '-'

    with args.output.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['page', 'pair', 'right', 'ndl', 'crop_text', 'crop', 'llm'])
        for case in cases:
            case['crop'] = crop_choice(case)
            writer.writerow([lines[case['index']]['page'], '/'.join(case['pair']), case['right'],
                             case['read'], case['crop_text'], case['crop'], case.get('llm', '')])

    for judge in ('ndl', 'crop', 'llm'):
        tally = Counter()
        for case in cases:
            if judge == 'llm' and 'llm' not in case:
                continue
            chosen = {'ndl': case['read'], 'crop': case['crop']}.get(judge, case.get('llm', '').strip('-'))
            group = 'ndl-wrong' if case['read'] != case['right'] else 'ndl-right'
            tally[(group, 'right' if chosen == case['right'] else 'none' if not chosen else 'wrong')] += 1
        for group in ('ndl-right', 'ndl-wrong'):
            n = sum(v for (g, _), v in tally.items() if g == group)
            print(f'{judge:5} {group:9} n={n:4}  right {tally[(group, "right")]:4}  '
                  f'wrong {tally[(group, "wrong")]:3}  no answer {tally[(group, "none")]:3}')


if __name__ == '__main__':
    main()
