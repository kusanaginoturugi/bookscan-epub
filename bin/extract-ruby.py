#!/usr/bin/env python3
"""Extract ruby annotations from NDLOCR-Lite XML blocks, split multi-ruby blocks by
vertical ink gaps, recognize with PARSeq 30-char model, and align base/ruby text
with a local LLM.

Writes work/epub/SLUG/ruby.tsv (cached by block id so reruns skip already-asked items).
"""

import argparse
import csv
import importlib.util
import json
import math
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pypdfium2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin'))
from text_layer_utils import catalog_pages, normalize_ruby, parse_pages  # noqa: E402

RUBY_COLS = ['id', 'page', 'line_id', 'y0', 'y1', 'char_pos', 'raw_ruby', 'snippet', 'base', 'ruby']
VALID_RUBY = re.compile(r'^[ぁ-ゖァ-ヺー・＝]+$')
HIRAGANA_RUBY = re.compile(r'^[ぁ-ゖー]+$')

PROMPT_MARK_RULE = '- 本文スニペットの⟦…⟧はルビの縦方向の位置から推定したおおよその該当範囲（1文字ほど前後する場合あり）。出力の親文字には⟦⟧を含めない。\n'

PROMPT = """日本語の縦書き小説の本文の一部と、そこに振られたルビ（振り仮名）のOCR読み取り結果です。
小説のルビには「難読漢字の読み（例: 恐懼→きようく、僥倖→ぎようこう）」だけでなく、「一般的な読みとは異なる表外訓・熟字訓・文語読み・当て字（例: 穿いた→は、傾げた→かし、解いて→ほど、詰る→なじ、敵わない→かな、刷いた→は、猛者→もさ、手袋→ガントレツト、背嚢→バツクパツク）」も多く使われます。
本文スニペットの中で、このルビが振られている正確な「親文字」と「正しいルビ」を判定してください。

ルール:
{mark_rule}- 親文字は本文スニペット{base_scope}に含まれる連続した部分文字列をそのまま抜き出す。
- 元ルビOCRの読み全体に対応する親文字（熟語）を丸ごと選び、熟語の一部だけに切り詰めたり、元ルビの読みを途中で削ったりしない。
- 送り仮名や、ルビが掛かっていない隣の漢字・助詞は親文字に含めない（例: 「瞬き」に「まばた」なら 瞬\tまばた、「微かな」に「かす」なら 微\tかす、「装置を潜り抜」に「くぐ」なら 潜\tくぐ、「粘り強く」に「ねば」なら 粘\tねば、「機を窺い」に「うかが」なら 窺\tうかが）。
- 元ルビOCRを尊重する。その漢字・文脈の特殊な読み・表外訓・熟字訓・当て字として成立するなら、一般的な読みに書き換えず元ルビOCRの読みをそのまま採用する。修正してよいのは濁点・半濁点や1文字程度の明らかなOCR誤認だけ。
- カタカナの当て字ルビはカタカナのまま残す。
- ルビはひらがな・カタカナとも小書き仮名（ぁぃぅぇぉっゃゅょゎ・ァィゥェォッャュョヮヵヶ）を使わず、すべて大きい仮名（あいうえおつやゆよわ・アイウエオツヤユヨワカケ）で書く（例: フィギュア→フイギユア、きょうく→きようく）。
- ルビOCRがゴミや汚れで本文と無関係なら NONE とだけ答える。
- 出力は必ず「親文字\tルビ」の1行のみ（説明不要）。

本文スニペット: {snippet}
ルビOCR: {raw_ruby}"""


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ask_llm(url, model, snippet, raw_ruby, marked=True):
    body = {
        'model': model,
        'temperature': 0,
        'max_tokens': 32,
        'chat_template_kwargs': {'enable_thinking': False},
        'messages': [{'role': 'user', 'content': PROMPT.format(
            mark_rule=PROMPT_MARK_RULE if marked else '',
            base_scope='（⟦⟧を除いた本文）' if marked else '',
            snippet=snippet, raw_ruby=raw_ruby)}],
    }
    req = urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = json.load(resp)['choices'][0]['message']['content'].strip()
    return text.splitlines()[0].strip() if text else 'NONE'


def split_ruby_crop(crop, min_gap=24):
    """Split a tall ruby block vertically wherever a white gap >= min_gap px separates rubies."""
    gray = crop.mean(axis=2) if crop.ndim == 3 else crop
    has_ink = (gray < 185).any(axis=1)
    segs = []
    in_seg = False
    start = gap = 0
    for y, ink in enumerate(has_ink):
        if ink:
            if not in_seg:
                start = y
                in_seg = True
            gap = 0
        elif in_seg:
            gap += 1
            if gap >= min_gap:
                end = y - gap + 1
                if end - start >= 8:
                    segs.append((start, end))
                in_seg = False
    if in_seg:
        end = len(has_ink) - gap
        if end - start >= 8:
            segs.append((start, end))
    return segs


def clean_pair(ans, snippet):
    """Validate and trim trailing okurigana from LLM's 'base\\truby' response."""
    if not ans or ans.upper().startswith('NONE') or '\t' not in ans:
        return '', ''
    base, ruby = (p.strip() for p in ans.split('\t', 1))
    if '⟦' not in snippet and '⟧' not in snippet:
        base = base.replace('⟦', '').replace('⟧', '')
    if not base or not ruby or base not in snippet:
        return '', ''
    # Ruby must consist only of kana / prolonged sound / middle dot (no kanji or digits)
    if not VALID_RUBY.fullmatch(ruby):
        return '', ''
    # Trim leading hiragana from base when ruby is pure hiragana and does not start with it (e.g. てて屯/たむろ -> 屯/たむろ)
    if HIRAGANA_RUBY.fullmatch(ruby):
        while len(base) >= 2 and ('\u3041' <= base[0] <= '\u3096') and not ruby.startswith(base[0]):
            base = base[1:]
    # Trim common trailing hiragana okurigana from both base and ruby (e.g. 掻いた/かいた -> 掻/か, 穿った/うがつた -> 穿/うが)
    trimmed_tail = False
    while (len(base) >= 2 and len(ruby) >= 2 and ('\u3041' <= base[-1] <= '\u3096') and
           (base[-1] == ruby[-1] or (trimmed_tail and base[-1] == 'っ' and ruby[-1] == 'つ'))):
        base, ruby = base[:-1], ruby[:-1]
        trimmed_tail = True
    # Trim trailing hiragana okurigana from base if it is not present at the end of ruby
    while len(base) >= 2 and ('\u3041' <= base[-1] <= '\u3096') and not ruby.endswith(base[-1]):
        if ('\u4e00' <= base[-2] <= '\u9fff') or base[-2] in '々〆ヶ' or ('\u3041' <= base[-2] <= '\u3096'):
            base = base[:-1]
        else:
            break
    ruby = normalize_ruby(ruby)
    # Base must contain at least one kanji or Latin letter (never pure kana/symbols)
    if not any(('\u4e00' <= c <= '\u9fff') or c in '々〆ヶ' or ('A' <= c <= 'Z') or ('a' <= c <= 'z') for c in base):
        return '', ''
    # Base should not be pure kana identical to ruby
    if base == ruby:
        return '', ''
    return base, ruby


def mark_snippet(ftext, char_pos, est_chars):
    """Build plain snippet (for TSV/validation) and position-marked snippet (for LLM prompt)."""
    m0 = max(0, min(len(ftext) - 1, char_pos))
    m1 = max(m0 + 1, min(len(ftext), m0 + max(1, est_chars)))
    i0 = max(0, m0 - 2)
    i1 = min(len(ftext), m1 + 2)
    snippet = ftext[i0:i1]
    if '⟦' in snippet or '⟧' in snippet:
        return snippet, snippet
    r0, r1 = m0 - i0, m1 - i0
    return snippet, snippet[:r0] + '⟦' + snippet[r0:r1] + '⟧' + snippet[r1:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work_dir', type=Path, help='work/searchable/SLUG directory')
    parser.add_argument('output_tsv', type=Path, help='output ruby.tsv')
    parser.add_argument('--source', required=True, help='source PDF filename')
    parser.add_argument('--url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    args = parser.parse_args()

    sensitivity = load_mod('ocr_sensitivity', ROOT / 'bin/ocr-sensitivity.py')
    ocr_dir = args.work_dir / 'ocr'
    ocr_stem = sorted(ocr_dir.glob('*.json'))[0].stem.rsplit('_', 1)[0]
    pdf_path = ROOT / 'input/bookscan-downloads' / args.source

    skip = catalog_pages(ocr_dir, ocr_stem)
    skip_file = args.work_dir / 'skip-pages.txt'
    if skip_file.exists():
        skip |= parse_pages(skip_file.read_text().strip())

    finals = {}
    with (args.work_dir / 'lines.tsv').open(newline='') as handle:
        for r in csv.DictReader(handle, delimiter='\t'):
            finals[(int(r['page']), int(r['line_id']))] = r['final']

    cached = {}
    if args.output_tsv.exists():
        with args.output_tsv.open(newline='') as handle:
            for r in csv.DictReader(handle, delimiter='\t'):
                cached[r['id']] = r

    # Pass 1: find all ruby segments and run PARSeq 30-char recognizer for uncached ones
    xmls = sorted(ocr_dir.glob(f'{ocr_stem}_*.xml'))
    segments = []
    doc = pypdfium2.PdfDocument(str(pdf_path))
    models = None
    for xml in xmls:
        page = int(xml.stem.rsplit('_', 1)[1])
        if page in skip:
            continue
        tree = ET.parse(xml)
        rubies = [b for b in tree.iter('BLOCK') if b.get('TYPE') == 'ルビ']
        if not rubies:
            continue
        lines = sensitivity.load_lines(xml)
        pimg = np.array(doc[page - 1].render(scale=300 / 72).to_pil().convert('RGB'))
        for b_idx, rb in enumerate(rubies):
            rx, ry, rw, rh = int(rb.get('X')), int(rb.get('Y')), int(rb.get('WIDTH')), int(rb.get('HEIGHT'))
            full_crop = sensitivity.crop(pimg, rx, ry, rw, rh)
            if not full_crop.size:
                continue
            for s_idx, (sy0, sy1) in enumerate(split_ruby_crop(full_crop)):
                sub_y0, sub_y1 = ry + sy0, ry + sy1
                best_l, best_dist = None, 9999
                for lid, ln in enumerate(lines):
                    if ln['h'] <= ln['w']:
                        continue
                    ov = min(sub_y1, ln['y'] + ln['h']) - max(sub_y0, ln['y'])
                    if ov <= 0:
                        continue
                    dist = rx - (ln['x'] + ln['w'])
                    if -15 <= dist <= 45 and abs(dist) < best_dist:
                        best_dist = abs(dist)
                        best_l = (lid, ln)
                if not best_l:
                    continue
                lid, ln = best_l
                ftext = finals.get((page, lid), ln['text'])
                if not ftext:
                    continue
                seg_id = f'{page}:{lid}:{sub_y0}-{sub_y1}'
                if seg_id in cached:
                    segments.append(cached[seg_id])
                    continue
                if models is None:
                    models = sensitivity.recognizers(args.device)
                sub_crop = full_crop[max(0, sy0 - 2):min(full_crop.shape[0], sy1 + 2), :]
                raw_rb = models[0].read(sub_crop).strip()
                if not raw_rb or not re.search(r'[ぁ-ゖァ-ヺ]', raw_rb):
                    continue
                per = ln['h'] / max(len(ftext), 1)
                p0 = (sub_y0 - ln['y']) / per
                p1 = (sub_y1 - ln['y']) / per
                char_pos = max(0, min(len(ftext) - 1, math.floor(p0 + 0.25)))
                end_pos = min(len(ftext), max(char_pos + 1, math.ceil(p1 - 0.25)))
                est_chars = end_pos - char_pos
                snippet, marked_snippet = mark_snippet(ftext, char_pos, est_chars)
                segments.append({
                    'id': seg_id, 'page': page, 'line_id': lid,
                    'y0': sub_y0, 'y1': sub_y1, 'char_pos': char_pos,
                    'raw_ruby': raw_rb, 'snippet': snippet,
                    'base': None, 'ruby': None,
                    'est_chars': est_chars, 'marked_snippet': marked_snippet,
                })
    doc.close()

    # Pass 2: ask LLM for uncached segments and append incrementally
    args.output_tsv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.output_tsv.exists()
    asked = valid = 0
    with args.output_tsv.open('a', newline='') as handle:
        writer = csv.DictWriter(handle, RUBY_COLS, delimiter='\t', lineterminator='\n', extrasaction='ignore')
        if write_header:
            writer.writeheader()
        for seg in segments:
            if seg['base'] is not None:
                if seg['base']:
                    valid += 1
                continue
            ans = ask_llm(args.url, args.model, seg['marked_snippet'], seg['raw_ruby'],
                          marked=seg['marked_snippet'] != seg['snippet'])
            base, ruby = clean_pair(ans, seg['snippet'])
            seg['base'], seg['ruby'] = base, ruby
            writer.writerow(seg)
            handle.flush()
            asked += 1
            if base:
                valid += 1
    print(f'ruby extracted: {valid} valid / {len(segments)} segments ({asked} asked now) -> {args.output_tsv}')


if __name__ == '__main__':
    main()
