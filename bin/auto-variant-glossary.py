#!/usr/bin/env python3
"""Pick dakuten/handakuten OCR variants (ボストン/ポストン) for --fix-variants with a local LLM.

Katakana words are counted as standalone tokens, with the same boundaries as
text_layer_utils.fix_variants. A pair is a candidate when both forms occur and
differ by one バ行/パ行 kana. The local LLM sees context lines for both forms and
answers which spelling is right or that both are distinct words. Each pair is
asked twice with the two forms swapped, because the models lean to the first
option; disagreeing answers count as no verdict. A term is
marked verified=yes only when the LLM picks the majority form for every pair
of that term; everything else stays in the TSV for review and is not used for
correction. On the Summer Door check, all 14 such pairs had the majority form
right on the page image.
"""

import argparse
import csv
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader


TOKEN = re.compile(r'[ァ-ヴー]+')
VOICING = str.maketrans('バビブベボパピプペポ', 'パピプペポバビブベボ')
PROMPT = """日本語の小説をOCRした本文に、濁点と半濁点だけが違う2つの表記が出てきます。
OCRは「バ」と「パ」のような濁点・半濁点をよく読み違えます。
文脈を読んで、次のどれかを答えてください。
A: 「{a}」が正しく、「{b}」は読み違い
B: 「{b}」が正しく、「{a}」は読み違い
BOTH: 2つは別の語で、どちらも正しい
人名や地名、外来語として自然なつづりかどうかも考えてください。

「{a}」の用例:
{ctx_a}

「{b}」の用例:
{ctx_b}

A、B、BOTHのどれか1語だけを答えてください。"""


def contexts(lines, word, limit=3):
    pattern = re.compile(rf'(?<![ァ-ヴー]){re.escape(word)}(?![ァ-ヴー])')
    found = []
    for line in lines:
        match = pattern.search(line)
        if match:
            start = max(0, match.start() - 30)
            found.append('・' + line[start:match.end() + 30])
            if len(found) == limit:
                break
    return '\n'.join(found)


def ask(url, model, prompt, think=False):
    # Gemma 4 thinks before answering unless told not to; with thinking on,
    # leave room so the answer is not cut off.
    body = {'model': model, 'temperature': 0, 'max_tokens': 4096 if think else 16,
            'messages': [{'role': 'user', 'content': prompt}]}
    if not think:
        body['chat_template_kwargs'] = {'enable_thinking': False}
    payload = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, payload, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=600) as response:
        answer = json.load(response)['choices'][0]['message']['content']
    answer = answer.strip().upper()
    for label in ('BOTH', 'A', 'B'):
        if answer.startswith(label):
            return label
    return 'UNCLEAR:' + answer[:20]


def judge(url, model, lines, major, minor, think=False):
    # Ask in both orders; small models tend to answer the first option, so the
    # majority form counts as chosen only when it wins from either position.
    first = ask(url, model, PROMPT.format(a=major, b=minor, ctx_a=contexts(lines, major),
                                          ctx_b=contexts(lines, minor)), think)
    second = ask(url, model, PROMPT.format(a=minor, b=major, ctx_a=contexts(lines, minor),
                                           ctx_b=contexts(lines, major)), think)
    swapped = {'A': 'B', 'B': 'A'}.get(second, second)
    return first if first == swapped else f'{first}/{swapped}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path, help='text-layer PDF before variant fixes')
    parser.add_argument('output', type=Path, help='glossary TSV for rebuild-text-layer.py')
    parser.add_argument('--min-length', type=int, default=3)
    parser.add_argument('--min-majority', type=int, default=3)
    parser.add_argument('--url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    parser.add_argument('--think', action='store_true',
                        help='let the model think first (much slower; 12B often ran past 2048 tokens)')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'output already exists: {args.output}')

    lines = []
    for page in PdfReader(args.pdf).pages:
        lines.extend(line for line in page.extract_text().split('\n') if line.strip())
    counts = Counter()
    for line in lines:
        counts.update(token for token in TOKEN.findall(line) if len(token) >= args.min_length)

    pairs = {}
    for word, count in counts.items():
        for index, char in enumerate(word):
            swapped = char.translate(VOICING)
            other = word[:index] + swapped + word[index + 1:]
            if swapped != char and other in counts and count > counts[other]:
                pairs[(word, other)] = (count, counts[other])
    # A minority form close to two majority words is ambiguous; keep both for review.
    minority_of = defaultdict(list)
    for major, minor in pairs:
        minority_of[minor].append(major)
    minorities = set(minority_of)

    # fix_variants rewrites every one-kana variant of a verified term, so a term
    # is verified only when all of its observed pairs pass.
    terms = {}
    for (major, minor), (major_count, minor_count) in sorted(pairs.items(), key=lambda item: -item[1][0]):
        answer = judge(args.url, args.model, lines, major, minor, args.think)
        reasons = []
        if answer != 'A':
            reasons.append(f'llm={answer}')
        if major_count < args.min_majority:
            reasons.append('rare')
        if len(minority_of[minor]) > 1:
            reasons.append('ambiguous')
        if major in minorities:
            reasons.append('term-is-minority-elsewhere')
        row = terms.setdefault(major, {'term': major, 'occurrences': major_count,
                                       'variants': [], 'reasons': []})
        row['variants'].append(f'{minor}({minor_count}):{answer}')
        row['reasons'].extend(f'{minor}:{reason}' for reason in reasons)
        print(f'{major}({major_count}) / {minor}({minor_count}): {answer} '
              f"{'-> use' if not reasons else 'skip ' + ','.join(reasons)}", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['term', 'occurrences', 'variants', 'verified', 'note'])
        for row in terms.values():
            writer.writerow([row['term'], row['occurrences'], ' '.join(row['variants']),
                             'no' if row['reasons'] else 'yes', ','.join(row['reasons'])])
    used = sum(not row['reasons'] for row in terms.values())
    print(f'{len(lines)} lines, {len(pairs)} pairs, {len(terms)} terms, {used} verified')


if __name__ == '__main__':
    main()
