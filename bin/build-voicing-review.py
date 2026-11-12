#!/usr/bin/env python
"""Settle dakuten/handakuten katakana spellings (バ/パ) and build a review page.

Run with .venv-ndl/bin/python. The NDLOCR-Lite text is what
bin/build-searchable-book.sh places (margin30 for lines >= --min-height,
--fix-dashes, --fix-brackets). A pair of katakana words that differ by one
バ行/パ行 kana is a candidate when both forms occur in the book (as in
bin/auto-variant-glossary.py), or when YomiToku (--yomitoku-dir) reads the
other form on the same page.

--auto-crop re-reads every occurrence cropped from the page at full
resolution with the NDLOCR-Lite recognizer (the line-wide read shrinks the
marks away). A pair is decided automatically only when the crop majority picks
one form, the crop never confirms the other form where NDL read it, and, for a
word seen once, YomiToku read the same form. Everything else waits for a
person. On The Door into Summer this decided pairs with no error against the
52 pairs the user settled (bin/check-voicing-judges.py).

The page shows each pair with a few occurrences (YomiToku disagreements first):
page thumbnail with a red box, the line, and the word at 3x. The reviewer picks
a spelling, "both" (two real words), or leaves the automatic decision; the page
exports the explicit choices as TSV (a, b, choice) for --human. --glossary-out
writes the glossary for rebuild-text-layer.py --glossary --fix-variants from
human choices, then automatic ones. A word kept as "both" is listed as verified
when it is a one-kana variant of a chosen term, so --fix-variants leaves it.
An older --human file in glossary form (term, variants, note keep:a/b) is read
as pair choices too.

usage: bin/build-voicing-review.py PDF OCR_DIR OUT_DIR --ocr-stem STEM
         [--recognized margin.tsv] [--yomitoku-dir DIR] [--auto-crop --device cuda]
         [--human pairs.tsv] [--glossary-out glossary.tsv] [--title NAME]
"""

import argparse
import csv
import html
import importlib.util
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pypdfium2
from PIL import ImageDraw

from text_layer_utils import catalog_pages, fix_brackets, fix_dashes, line_joins, parse_pages

TOKEN = re.compile(r"(?<![ァ-ヴー])(?![ァィゥェォッャュョヮヵヶー])[ァ-ヴ][ァ-ヴー]*")  # no ー or small kana first
VOICED = set('バビブベボパピプペポ')
VOICING = str.maketrans('バビブベボパピプペポ', 'パピプペポバビブベボ')
WS = re.compile(r'\s+')


def variants(word):
    for index, char in enumerate(word):
        if char in VOICED:
            yield word[:index] + char.translate(VOICING) + word[index + 1:]


def load_lines(args):
    recognized = {}
    if args.recognized:
        with args.recognized.open(newline='') as handle:
            recognized = {(int(r['page']), int(r['line_id'])): r for r in csv.DictReader(handle, delimiter='\t')}
    lines = []
    for path in sorted(args.ocr_dir.glob(f'{args.ocr_stem}_*.json')):
        page = int(path.stem.rsplit('_', 1)[1])
        items = json.loads(path.read_text())['contents'][0]
        joins = line_joins([item['text'] for item in items])
        for position, item in enumerate(items):
            text = item['text']
            row = recognized.get((page, int(item['id'])))
            if row and int(row['height']) >= args.min_height:
                text = row[args.column]
            vertical = item['isVertical'] == 'true'
            if vertical:
                text = fix_brackets(fix_dashes(text))
            xs = [p[0] for p in item['boundingBox']]
            ys = [p[1] for p in item['boundingBox']]
            lines.append(dict(page=page, line_id=int(item['id']), box=(min(xs), min(ys), max(xs), max(ys)),
                              vertical=vertical, text=text, joined=joins[position]))
    return lines


def load_yomitoku(directory):
    pages = {}
    for path in directory.glob('*_p*.json'):
        page = int(path.stem.rsplit('_p', 1)[1])
        data = json.loads(path.read_text())
        pages[page] = WS.sub('', ''.join(p['contents'] or '' for p in data['paragraphs']))
    return pages


def find_pairs(lines, yomitoku, min_length):
    occurrences = defaultdict(list)  # word -> [(line index, start, end)]
    for index, line in enumerate(lines):
        text = line['text']
        lo, hi = len(text) - len(text.lstrip()), len(text.rstrip())
        for match in TOKEN.finditer(text):
            # A word running over the line break (パター|ン) is not a word of its
            # own at either side; a lone kana is usually such a piece too, and as
            # a glossary term it would rewrite every standalone ボ in the book.
            if (line['joined'][0] and match.start() == lo) or (line['joined'][1] and match.end() == hi):
                continue
            if len(match.group()) >= 2 and VOICED & set(match.group()):
                occurrences[match.group()].append((index, match.start(), match.end()))
    pairs = {}
    for word, places in occurrences.items():
        for other in variants(word):
            key = tuple(sorted((word, other)))
            if other in occurrences and len(word) >= min_length:
                pairs.setdefault(key, {'yomi': {}})
            for index, start, end in places:
                text = yomitoku.get(lines[index]['page'])
                if text and word not in text and other in text:
                    pairs.setdefault(key, {'yomi': {}})['yomi'][(index, start)] = other
    return occurrences, pairs


def crop_readings(lines, places, pdf, device, margin=0.3):
    """{(line index, start, end): text} from re-reading each word crop alone."""
    spec = importlib.util.spec_from_file_location('ocr_sensitivity', Path(__file__).resolve().parent / 'ocr-sensitivity.py')
    sensitivity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sensitivity)
    models = sensitivity.recognizers(device)
    by_page = defaultdict(list)
    for place in sorted(places):
        by_page[lines[place[0]]['page']].append(place)
    readings = {}
    document = pypdfium2.PdfDocument(str(pdf))
    for page, items in sorted(by_page.items()):
        image = np.array(document[page - 1].render(scale=300 / 72).to_pil().convert('RGB'))
        crops_ = []
        for n, (index, start, end) in enumerate(items):
            line = lines[index]
            x0, y0, x1, y1 = line['box']
            size = max(len(line['text']), 1)
            if line['vertical']:
                per = (y1 - y0) / size
                box = (x0 - 4, y0 + (start - margin) * per, x1 + 4, y0 + (end + margin) * per)
            else:
                per = (x1 - x0) / size
                box = (x0 + (start - margin) * per, y0 - 4, x0 + (end + margin) * per, y1 + 4)
            bx0, by0, bx1, by1 = (int(v) for v in box)
            crops_.append((n, image[max(0, by0):by1, max(0, bx0):bx1], end - start))
        texts = sensitivity.recognize(models, crops_)
        for n, place in enumerate(items):
            readings[place] = texts[n]
    document.close()
    return readings


def auto_decide(key, occurrences, readings, yomi_read):
    """(choice, note) from crop re-reads, or (None, note) when not safe to decide."""
    votes = Counter()
    confirmed_loser = defaultdict(int)
    for word in key:
        for place in occurrences.get(word, []):
            hits = [w for w in key if w in readings.get(place, '')]
            if len(hits) == 1:
                votes[hits[0]] += 1
                if hits[0] == word:
                    confirmed_loser[word] += 1  # crop agrees with what NDL read here
    a, b = key
    note = f'crop {a}:{votes[a]} {b}:{votes[b]}'
    if votes[a] == votes[b]:
        return None, note
    winner, loser = (a, b) if votes[a] > votes[b] else (b, a)
    if confirmed_loser[loser]:
        return None, note + f'; crop confirms {loser} {confirmed_loser[loser]}x'
    seen = [place for word in key for place in occurrences.get(word, [])]
    if len(seen) == 1 and yomi_read.get(seen[0]) != winner:
        return None, note + '; seen once, YomiToku differs'
    return winner, note


def base(word):
    """The word with dakuten and handakuten removed (バルブ, パルプ -> ハルフ)."""
    return ''.join(unicodedata.normalize('NFD', ch)[0] for ch in word)


def agreed_fixes(occurrences, readings, yomitoku, lines, settled):
    """{place: spelling} where the word-crop re-read and YomiToku agree on another
    spelling that differs from NDL's only in dakuten/handakuten. Places of
    pairs already settled (auto or human) are left to the glossary."""
    fixes = {}
    for word, places in occurrences.items():
        for place in places:
            if place in settled:
                continue
            page_text = yomitoku.get(lines[place[0]]['page'], '')
            for token in TOKEN.findall(readings.get(place, '')):
                if token != word and len(token) == len(word) and base(token) == base(word) and token in page_text:
                    fixes[place] = token
                    break
    return fixes


def load_human(path):
    """{(a, b): choice} from the page TSV (a, b, choice) or an older glossary TSV."""
    decisions = {}
    if not path or not path.exists():
        return decisions
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    for row in rows:
        if 'choice' in row and row['a'].startswith('fix:'):
            decisions[('fix:', row['a'][4:])] = row['choice']  # keep / reject an automatic fix
            continue
        if 'choice' in row:
            decisions[tuple(sorted((row['a'], row['b'])))] = row['choice']
            continue
        for variant in filter(None, (row.get('variants') or '').split(' ')):
            decisions[tuple(sorted((row['term'], variant.split('(')[0])))] = row['term']
        for note in filter(None, (row.get('note') or '').split(',')):
            if note.startswith('keep:'):
                decisions[tuple(sorted(note[5:].split('/')))] = 'both'
    return decisions


def glossary_rows(decisions, sources, occurrences):
    winners = {}
    for key, choice in decisions.items():
        if choice == 'both':
            continue
        loser = key[0] if choice == key[1] else key[1]
        winners.setdefault(choice, []).append(f'{loser}({len(occurrences.get(loser, []))}):{sources[key]}')
    losers = {k[0] if c == k[1] else k[1] for k, c in decisions.items() if c != 'both'}
    clash = sorted(set(winners) & losers)
    rows = {term: [term, len(occurrences.get(term, [])), ' '.join(v), 'yes', '']
            for term, v in winners.items() if len(term) >= 2}
    for key, choice in decisions.items():
        if choice != 'both':
            continue
        for word in key:
            if any(t != word and word in variants(t) for t in winners) and word not in rows:
                rows[word] = [word, len(occurrences.get(word, [])), '', 'yes', f'keep:{key[0]}/{key[1]}']
    return list(rows.values()), clash


def pick_examples(pair, info, occurrences, limit):
    chosen = [(index, start, end) for word in pair for index, start, end in occurrences.get(word, [])
              if (index, start) in info['yomi']][:limit - 2]
    pools = [[o for o in occurrences.get(word, []) if o not in chosen] for word in pair]
    while len(chosen) < limit and any(pools):
        for pool in pools:
            if pool and len(chosen) < limit:
                # spread picks over the book
                chosen.append(pool.pop(len(pool) // 2 if len(pool) > 2 else 0))
    return sorted(chosen)


def crops(image, line, start, end):
    x0, y0, x1, y1 = line['box']
    size = max(len(line['text']), 1)
    if line['vertical']:
        per = (y1 - y0) / size
        word = (x0 - 8, y0 + start * per - per * 0.6, x1 + 8, y0 + end * per + per * 0.6)
        context = (x0 - 30, y0 + (start - 7) * per, x1 + 30, y0 + (end + 7) * per)
    else:
        per = (x1 - x0) / size
        word = (x0 + start * per - per * 0.6, y0 - 8, x0 + end * per + per * 0.6, y1 + 8)
        context = (x0 + (start - 7) * per, y0 - 30, x0 + (end + 7) * per, y1 + 30)
    clip = lambda r: tuple(int(v) for v in (max(0, r[0]), max(0, r[1]), min(image.width, r[2]),
                                            min(image.height, r[3])))
    zoom = image.crop(clip(word))
    zoom = zoom.resize((zoom.width * 3, zoom.height * 3))
    thumb = image.convert('RGB')
    ImageDraw.Draw(thumb).rectangle(clip(word), outline=(220, 0, 0), width=12)
    thumb.thumbnail((240, 340))
    return thumb, image.crop(clip(context)), zoom


PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:15px sans-serif;margin:1.5em;color:#222;background:#fff}}
section{{border-top:2px solid #999;padding:.8em 0}}
h2{{font-size:1.25em;margin:.2em 0}} .opts label{{margin-right:1.2em;font-size:1.1em}}
.ex{{display:flex;flex-wrap:wrap;gap:14px;margin-top:.6em}}
figure{{margin:0;border:1px solid #ccc;padding:6px;display:flex;gap:6px;align-items:flex-start}}
figcaption{{font-size:13px;writing-mode:horizontal-tb;max-width:9em}}
.disagree{{border-color:#d33}} textarea{{width:100%;height:14em;font-family:monospace}}
#out{{position:sticky;bottom:0;background:#f4f4f4;padding:.6em;border-top:2px solid #999}}
.done h2{{color:#777}}
</style>
<h1>{title}</h1>
<p>濁点・半濁点だけが違うカタカナ語の組。各組で原本画像と同じつづりを選ぶ。<b>赤枠</b>はYomiTokuが別のつづりで読んだ箇所。
「両方正しい」は別の語として両方を残す。「どちらも違う」は横の欄に正しい綴りを書く（その組の全箇所を直す）。画面下の「次の未確認」（キーは n、戻るは p）で、自動判定のない組を順に回れる。<b>自動</b>と書いた組は、語を切り出して読み直した結果で決めてある（選ばなければそのまま使う）。
人が選んだものだけが下のTSV（a、b、choice）にまとまるので、コピーして <code>{human}</code> に保存し、作り直す。
人の選択は常に自動判定より優先する。選択はこのブラウザにも一時保存される。</p>
<p>{summary}</p>
{sections}
<div id="out"><button id="prev">↑ 前の未確認（p）</button> <button id="next">次の未確認 ↓（n）</button> <label><input type="checkbox" id="advance" checked>選んだら次の未確認へ進む</label> <button id="copy">TSVをコピー</button> → 保存: <code id="save">wl-paste &gt; {human}</code> <span id="status"></span>
<textarea id="tsv" readonly></textarea></div>
<script>
const PAIRS = {pairs};
const KEY = 'voicing-review:' + document.title;
let saved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function choice(i) {{
  const el = document.querySelector('input[name="p' + i + '"]:checked');
  return el ? el.value : 'undecided';
}}
function render() {{
  // Only explicit choices are stored and exported, keyed by the pair.
  const store = {{}};
  const lines = ['a\\tb\\tchoice'];
  const winners = new Set(), losers = new Set();
  PAIRS.forEach((p, i) => {{
    const v = choice(i);
    document.getElementById('s' + i).classList.toggle('done', v !== 'undecided' || !!p.auto);
    if (v === 'undecided') return;
    if (p.kind === 'fix') {{
      store['fix:' + p.id] = v;
      lines.push(['fix:' + p.id, '', v].join('\\t'));
      return;
    }}
    const typed = document.getElementById('c' + i).value.trim();
    if (v === 'custom' && !typed) return;
    store[p.a + '/' + p.b] = v === 'custom' ? 'custom:' + typed : v;
    const word = v === 'a' ? p.a : v === 'b' ? p.b : v === 'custom' ? typed : 'both';
    lines.push([p.a, p.b, word].join('\\t'));
    if (v === 'a' || v === 'b') {{ winners.add(word); losers.add(word === p.a ? p.b : p.a); }}
  }});
  try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (e) {{}}
  document.getElementById('tsv').value = lines.join('\\n') + '\\n';
  const clash = [...winners].filter(t => losers.has(t));
  document.getElementById('status').textContent = (lines.length - 1) + ' 組を人が選択（'
    + PAIRS.filter(p => p.auto).length + ' 組は自動判定あり、' + PAIRS.filter(p => p.kind !== 'fix').length + ' 組中）'
    + (clash.length ? '。矛盾（正しいと誤りの両方に選んだ語）: ' + clash.join('、') : '');
}}
PAIRS.forEach((p, i) => {{
  let v = saved[p.kind === 'fix' ? 'fix:' + p.id : p.a + '/' + p.b] || p.human;
  if (v && v.startsWith('custom:') && p.kind !== 'fix') {{ document.getElementById('c' + i).value = v.slice(7); v = 'custom'; }}
  if (v) {{ const el = document.querySelector('input[name="p' + i + '"][value="' + v + '"]'); if (el) el.checked = true; }}
}});
document.addEventListener('change', render);
document.addEventListener('input', render);
const COUNT = PAIRS.length;
const OPEN = i => choice(i) === 'undecided' && !PAIRS[i].auto;
function isOpen(i) {{ return OPEN(i); }}
function jump(from, dir) {{
  for (let i = from + dir; i >= 0 && i < COUNT; i += dir) {{
    if (isOpen(i)) {{ document.getElementById('s' + i).scrollIntoView({{behavior: 'smooth', block: 'start'}}); return; }}
  }}
  document.getElementById('status').textContent = dir > 0 ? 'この先に未確認はない' : 'この前に未確認はない';
}}
function current() {{
  let at = -1;
  for (let i = 0; i < COUNT; i++) {{
    if (document.getElementById('s' + i).getBoundingClientRect().top <= 12) at = i; else break;
  }}
  return at;
}}
document.getElementById('next').addEventListener('click', () => jump(current(), 1));
document.getElementById('prev').addEventListener('click', () => jump(Math.max(current(), 0), -1));
document.querySelectorAll('a.go').forEach(a => a.addEventListener('click', e => {{ e.preventDefault(); jump(Number(a.dataset.n), 1); }}));
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' && e.target.type === 'text' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'n') jump(current(), 1);
  if (e.key === 'p') jump(Math.max(current(), 0), -1);
}});
document.addEventListener('change', e => {{
  // After a choice, move on to the next item that still needs a look.
  const m = e.target.name && e.target.name.match(/^[hp](\\d+)$/);
  if (m && e.target.value !== 'undecided' && document.getElementById('advance').checked) setTimeout(() => jump(Number(m[1]), 1), 150);
}});

document.getElementById('copy').addEventListener('click', async () => {{
  const area = document.getElementById('tsv');
  try {{ await navigator.clipboard.writeText(area.value); }}
  catch (e) {{ area.select(); document.execCommand('copy'); }}
  document.getElementById('status').textContent += '（コピーした）';
}});
render();
</script>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pdf', type=Path, help='PDF that was given to ocr.py (same pixel coordinates)')
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('output', type=Path, help='directory for index.html and images')
    parser.add_argument('--ocr-stem', required=True)
    parser.add_argument('--recognized', type=Path, help='TSV from bin/margin-recognize.py')
    parser.add_argument('--column', default='margin30')
    parser.add_argument('--min-height', type=int, default=400)
    parser.add_argument('--yomitoku-dir', type=Path, help='yomitoku -f json output for the same PDF')
    parser.add_argument('--min-length', type=int, default=3,
                        help='shortest word for pairs found only inside the NDL text')
    parser.add_argument('--examples', type=int, default=6)
    parser.add_argument('--title', default='濁点・半濁点のレビュー')
    parser.add_argument('--auto-crop', action='store_true', help='decide safe pairs from word-crop re-reads')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--human', type=Path, help='pair choices saved from the page (a, b, choice)')
    parser.add_argument('--glossary-out', type=Path, help='glossary TSV for --glossary --fix-variants')
    parser.add_argument('--corrections-out', type=Path,
                        help='per-place corrections (crop and YomiToku agree; "both wrong" choices) '
                             'for rebuild-text-layer.py --word-corrections')
    parser.add_argument('--skip-pages', help="pages left out of the review, e.g. '312-322'")
    parser.add_argument('--skip-catalog', action='store_true',
                        help="also leave out back-matter catalog pages (text_layer_utils.catalog_pages)")
    args = parser.parse_args()

    skip = parse_pages(args.skip_pages) | (catalog_pages(args.ocr_dir, args.ocr_stem) if args.skip_catalog else set())
    lines = [line for line in load_lines(args) if line['page'] not in skip]
    if skip:
        print(f'pages left out: {",".join(map(str, sorted(skip)))}')
    yomitoku = load_yomitoku(args.yomitoku_dir) if args.yomitoku_dir else {}
    occurrences, pairs = find_pairs(lines, yomitoku, args.min_length)
    yomi_read = {}
    for key, info in pairs.items():
        for word in key:
            for place in occurrences.get(word, []):
                text = yomitoku.get(lines[place[0]]['page'], '')
                yomi_read[place] = info['yomi'].get(place[:2]) or (word if word in text else None)
    human = load_human(args.human)
    fix_verdicts = {k[1]: human.pop(k) for k in [k for k in human if k[0] == 'fix:']}
    # Candidates only change with the code (e.g. words split over a line break
    # stopped being pairs); report choices for pairs that are gone and skip them.
    unknown = [k for k in human if k not in pairs]
    for key in unknown:
        print(f'warning: human choice for a pair that is no longer a candidate: {"/".join(key)} -> {human.pop(key)}')
    auto, notes, fixes = {}, {}, {}
    if args.auto_crop:
        # Every katakana word with dakuten/handakuten is re-read from a full-size
        # crop: the line-wide read shrinks the marks away.
        places = {place for word in occurrences for place in occurrences[word]}
        readings = crop_readings(lines, places, args.pdf, args.device)
        for key in pairs:
            auto[key], notes[key] = auto_decide(key, occurrences, readings, yomi_read)
    custom = {k: c for k, c in human.items() if c != 'both' and c not in k}
    decisions = {k: c for k, c in auto.items() if c}
    sources = {k: 'auto' for k in decisions}
    decisions.update({k: c for k, c in human.items() if k not in custom})
    sources.update({k: 'human' for k in human if k not in custom})
    for key in custom:
        decisions.pop(key, None)
    settled = {place for key in list(decisions) + list(custom) for word in key for place in occurrences.get(word, [])}
    def fix_id(place, to):
        index, start, end = place
        line = lines[index]
        text = line['text']
        return f'{line["page"]}:{line["line_id"]}:{text[max(0, start - 4):start]}[{text[start:end]}→{to}]{text[end:end + 4]}'

    agreed = {}
    if args.auto_crop:
        agreed = agreed_fixes(occurrences, readings, yomitoku, lines, settled)
        fixes = {place: (to, 'auto-agree') for place, to in agreed.items()
                 if fix_verdicts.get(fix_id(place, to)) != 'reject'}
    for gone in set(fix_verdicts) - {fix_id(place, to) for place, to in agreed.items()}:
        print(f'warning: human verdict for an automatic fix that is no longer proposed: {gone}')
    for key, to in custom.items():
        for word in key:
            for place in occurrences.get(word, []):
                fixes[place] = (to, 'human')
    if args.corrections_out:
        with args.corrections_out.open('w', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            writer.writerow(['id', 'page', 'line_id', 'left', 'from', 'to', 'right', 'source'])
            for (index, start, end), (to, source) in sorted(fixes.items()):
                line = lines[index]
                text = line['text']
                left, right = text[max(0, start - 4):start], text[end:end + 4]
                writer.writerow([fix_id((index, start, end), to),
                                 line['page'], line['line_id'], left, text[start:end], to, right, source])
    sources_count = Counter(source for _, source in fixes.values())
    print(f'corrections: {sources_count["auto-agree"]} where crop and YomiToku agree, '
          f'{sources_count["human"]} from "both wrong" choices')
    if args.glossary_out:
        rows, clash = glossary_rows(decisions, sources, occurrences)
        if clash:
            raise SystemExit(f'a word is both chosen and rejected: {clash}')
        with args.glossary_out.open('w', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            writer.writerow(['term', 'occurrences', 'variants', 'verified', 'note'])
            writer.writerows(rows)
    order = sorted(pairs, key=lambda k: (-len(pairs[k]['yomi']),
                                         -sum(len(occurrences.get(w, [])) for w in k), k))
    examples = {key: pick_examples(key, pairs[key], occurrences, args.examples) for key in order}
    # Automatic fixes are shown after the pairs, one place each, to keep or reject.
    fix_items = sorted(agreed.items())
    for place, to in fix_items:
        examples[('fix:', fix_id(place, to))] = [place]
    order_all = order + [('fix:', fix_id(place, to)) for place, to in fix_items]

    images = args.output / 'img'
    images.mkdir(parents=True, exist_ok=True)
    by_page = defaultdict(list)
    for n, key in enumerate(order_all):
        for k, (index, start, end) in enumerate(examples[key]):
            by_page[lines[index]['page']].append((n, k, index, start, end))
    document = pypdfium2.PdfDocument(str(args.pdf))
    for page, items in sorted(by_page.items()):
        image = document[page - 1].render(scale=300 / 72).to_pil().convert('L')
        for n, k, index, start, end in items:
            for kind, picture in zip(('thumb', 'line', 'zoom'), crops(image, lines[index], start, end)):
                picture.save(images / f'{n}-{k}-{kind}.jpg', quality=85)
    document.close()

    # Section ids follow the page order: pairs first, then the automatic fixes.
    sections, data, fix_data = [], [], []
    for n, (place, to) in enumerate(fix_items, len(order)):
        fid = fix_id(place, to)
        index, start, end = place
        line = lines[index]
        word = line['text'][start:end]
        verdict = fix_verdicts.get(fid, '')
        fix_data.append({'kind': 'fix', 'id': fid, 'a': word, 'b': to, 'auto': '', 'human': verdict})
        options = ''.join(f'<label><input type="radio" name="p{n}" value="{value}">{label}</label>'
                          for value, label in (('keep', f'直す（{html.escape(to)}）'),
                                               ('reject', f'直さない（{html.escape(word)}のまま）'),
                                               ('undecided', '未確認（直したまま）')))
        fix_sections_html = (
            f'<section id="s{n}" class="fix"><h2>#{n + 1} 自動修正 {line["page"]}ページ: '
            f'{html.escape(word)} → {html.escape(to)}</h2><p>語を切り出して読み直した結果とYomiTokuが、'
            f'NDLと違う同じ綴りを読んだので直した。原本と違えば「直さない」を選ぶ。</p>'
            f'<div class="opts">{options} <a href="#" class="go" data-n="{n}">次の未確認へ ↓</a></div>'
            f'<div class="ex"><figure><img src="img/{n}-0-thumb.jpg"><img src="img/{n}-0-line.jpg">'
            f'<img src="img/{n}-0-zoom.jpg"><figcaption>{line["page"]}ページ<br>NDL: {html.escape(word)}'
            f'<br>直した綴り: {html.escape(to)}</figcaption></figure></div></section>')
        sections.append(fix_sections_html)
    fix_count = len(sections)
    sections_pairs = []
    for n, key in enumerate(order):
        a, b = key
        ca, cb = len(occurrences.get(a, [])), len(occurrences.get(b, []))
        choice = human.get(key)
        data.append({'a': a, 'b': b, 'ca': ca, 'cb': cb, 'auto': auto.get(key) or '',
                     'human': {a: 'a', b: 'b', 'both': 'both'}.get(choice, f'custom:{choice}' if choice else '')})
        agreed = sorted({fixes[place][0] for w in key for place in occurrences.get(w, []) if place in fixes})
        figures = []
        for k, (index, start, end) in enumerate(examples[key]):
            line = lines[index]
            word = line['text'][start:end]
            other = pairs[key]['yomi'].get((index, start))
            caption = (f'{line["page"]}ページ<br>NDL: {html.escape(word)}'
                       + (f'<br>YomiToku: {html.escape(other)}' if other else ''))
            figures.append(f'<figure class="{"disagree" if other else ""}">'
                           f'<img src="img/{n}-{k}-thumb.jpg"><img src="img/{n}-{k}-line.jpg">'
                           f'<img src="img/{n}-{k}-zoom.jpg"><figcaption>{caption}</figcaption></figure>')
        decided = auto.get(key)
        rest = f'自動判定に任せる（{html.escape(decided)}）' if decided else '保留（自動判定なし）'
        options = ''.join(f'<label><input type="radio" name="p{n}" value="{value}">{label}</label>'
                          for value, label in (('a', html.escape(a)), ('b', html.escape(b)),
                                               ('both', '両方正しい'), ('custom', 'どちらも違う:'),
                                               ('undecided', rest)))
        options += f'<input id="c{n}" size="10" placeholder="正しい綴り"> '
        if agreed:
            rest_note = f'切り出しとYomiTokuが一致した箇所は {html.escape("・".join(agreed))} に直す。'
        else:
            rest_note = ''
        sections_pairs.append(f'<section id="s{n}"><h2>#{n + 1} {html.escape(a)}（{ca}）／{html.escape(b)}（{cb}）'
                        f'{"【自動】" if decided else ""}</h2><p>YomiTokuと割れた箇所: {len(pairs[key]["yomi"])}。{html.escape(notes.get(key, ""))}{rest_note}</p>'
                        f'<div class="opts">{options} <a href="#" class="go" data-n="{n}">次の未確認へ ↓</a></div>'
                        f'<div class="ex">{"".join(figures)}</div></section>')
    data.extend(fix_data)
    auto_count = sum(1 for k in order if auto.get(k))
    summary = (f'{len(order)}組。うちYomiTokuと割れた組 {sum(bool(pairs[k]["yomi"]) for k in order)}。'
               f'自動判定 {auto_count}組、人の選択 {len(human)}組、どちらもない {sum(1 for k in order if not auto.get(k) and k not in human)}組。'
               f'自動修正 {fix_count}か所（最後に並ぶ）。本文の行 {len(lines)}、YomiTokuのページ {len(yomitoku)}。')
    (args.output / 'index.html').write_text(PAGE.format(
        title=html.escape(args.title), summary=summary, sections='\n'.join(sections_pairs + sections),
        human=html.escape(str(args.human.resolve() if args.human else '人の選択TSV')), pairs=json.dumps(data, ensure_ascii=False)), encoding='utf-8')
    print(summary)
    print(f'{len(order)} pairs, {sum(len(v) for v in examples.values())} examples: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
