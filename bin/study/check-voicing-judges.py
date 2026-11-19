#!/usr/bin/env python
"""Score automatic judges of dakuten/handakuten spellings against a human glossary.

Run with .venv-ndl/bin/python. Candidate pairs and their occurrences come from
bin/build-voicing-review.py (same text rules). A pair settled in the human
glossary TSV (from the review page) gives the right spelling of every
occurrence of either form. Judges:

  ndl      the NDLOCR-Lite reading in the text layer (baseline)
  crop     NDLOCR-Lite recognizer run again on the word alone, cropped from the
           page at full resolution, so the marks are not shrunk with the line
  margin   the same crop, scored for both spellings: log P of the read mark minus
           log P of the other mark at its position, summed over the three
           recognizers (build-voicing-review.voicing_margins); the sign picks
  llm      a local LLM shown the passage (previous, current and next line) with
           each spelling, asked in both orders which reads as natural Japanese;
           an answer counts only when both orders agree (--llm-sample of the
           cases NDL read right are asked, and every case NDL read wrong;
           --llm-undecided asks every case of the pairs margin_decide leaves
           to a person)

--pairs-out writes one row per candidate pair with the right answer (a
spelling, "both" from --human choices, or empty) and the summed margins, next
to the automatic decision (build-voicing-review.margin_decide: a spelling,
"both", or empty for a person).

usage: bin/study/check-voicing-judges.py PDF OCR_DIR GLOSSARY OUT.tsv --ocr-stem STEM
         [--recognized margin.tsv] [--yomitoku-dir DIR] [--human pairs.tsv] [--pairs-out pairs.tsv]
"""

import argparse
import csv
import importlib.util
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]  # bin/, whose modules this script loads
sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = load_module('voicing_review', ROOT / 'build-voicing-review.py')
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
    parser.add_argument('--human', type=Path, help='pair choices saved from the review page ("both" pairs)')
    parser.add_argument('--pairs-out', type=Path, help='per-pair TSV of summed margins')
    parser.add_argument('--llm-sample', type=int, default=0,
                        help='cases NDL read right to ask the LLM (all wrong ones are asked when > 0)')
    parser.add_argument('--llm-undecided', action='store_true',
                        help='ask the LLM about every occurrence of pairs with no strong margin')
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

    places = {(index, start, end) for key in pairs for word in key
              for index, start, end in occurrences.get(word, [])}
    readings, margins = review.crop_readings(lines, places, args.pdf, args.device, args.margin)
    for case in cases:
        place = (case['index'], case['start'], case['end'])
        case['crop_text'] = readings.get(place, '')
        a, b = case['pair']
        k = next(i for i in range(len(a)) if a[i] != b[i])
        m = margins.get(place, {}).get(k)
        case['margin'] = '' if m is None else m
        case['margin_choice'] = '' if m is None else case['read'] if m > 0 else next(w for w in case['pair'] if w != case['read'])

    def crop_choice(case):
        a, b = case['pair']
        text = case['crop_text']
        hits = [w for w in (a, b) if w in text]
        return hits[0] if len(hits) == 1 else ''

    asked = []
    if args.llm_sample:
        right = [c for c in cases if c['read'] == c['right']]
        asked = [c for c in cases if c['read'] != c['right']]
        asked += random.Random(20261111).sample(right, min(args.llm_sample, len(right)))
    if args.llm_undecided:
        undecided = {key for key in pairs if review.margin_decide(key, occurrences, margins)[0] is None}
        asked = [c for c in cases if c['pair'] in undecided]
    if asked:
        llm = review.LlmVoicing(args.llm_url, args.llm_model, None)
        for case in asked:
            place = (case['index'], case['start'], case['end'])
            case['llm'] = llm.choose(lines, place, case['pair']) or '-'

    with args.output.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['page', 'pair', 'right', 'ndl', 'crop_text', 'crop', 'margin', 'llm'])
        for case in cases:
            case['crop'] = crop_choice(case)
            writer.writerow([lines[case['index']]['page'], '/'.join(case['pair']), case['right'],
                             case['read'], case['crop_text'], case['crop'],
                             '' if case['margin'] == '' else f"{case['margin']:.2f}", case.get('llm', '')])

    for judge in ('ndl', 'crop', 'margin', 'llm'):
        tally = Counter()
        for case in cases:
            if judge == 'llm' and 'llm' not in case:
                continue
            chosen = {'ndl': case['read'], 'crop': case['crop'], 'margin': case['margin_choice']}.get(
                judge, case.get('llm', '').strip('-'))
            group = 'ndl-wrong' if case['read'] != case['right'] else 'ndl-right'
            tally[(group, 'right' if chosen == case['right'] else 'none' if not chosen else 'wrong')] += 1
        for group in ('ndl-right', 'ndl-wrong'):
            n = sum(v for (g, _), v in tally.items() if g == group)
            print(f'{judge:6} {group:9} n={n:4}  right {tally[(group, "right")]:4}  '
                  f'wrong {tally[(group, "wrong")]:3}  no answer {tally[(group, "none")]:3}')

    if args.pairs_out:
        write_pairs(args.pairs_out, pairs, occurrences, truth, review.load_human(args.human), margins, lines)


def write_pairs(path, pairs, occurrences, truth, human, margins, lines):
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['a', 'b', 'right', 'ca', 'cb', 'scored', 'sum', 'toward_a', 'toward_b',
                         'min', 'max', 'yomi_differs', 'auto', 'note', 'fixes'])
        for key in sorted(pairs):
            a, b = key
            k = next(i for i in range(len(a)) if a[i] != b[i])
            # Each occurrence's margin turned into evidence for a (positive) or b.
            votes = [(margins[p][k] if w == a else -margins[p][k])
                     for w in key for p in occurrences.get(w, []) if k in margins.get(p, {})]
            auto, note = review.margin_decide(key, occurrences, margins)
            right = 'both' if human.get(key) == 'both' else truth.get(key, '')
            # Places the image alone fixes when the pair is both words (no LLM here).
            fixes = review.place_fixes(key, occurrences, margins, lines, None) if 'both' in (auto, right) else {}
            writer.writerow([a, b, right, len(occurrences.get(a, [])), len(occurrences.get(b, [])), len(votes),
                             f'{sum(votes):.2f}', sum(v > 0 for v in votes), sum(v < 0 for v in votes),
                             f'{min(votes):.2f}' if votes else '', f'{max(votes):.2f}' if votes else '',
                             len(pairs[key]['yomi']), auto or '', note, len(fixes)])


if __name__ == '__main__':
    main()
