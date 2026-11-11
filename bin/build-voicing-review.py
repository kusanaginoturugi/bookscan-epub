#!/usr/bin/env python
"""Build a local review page for dakuten/handakuten katakana spellings (バ/パ).

Run with .venv-ndl/bin/python. The NDLOCR-Lite text is what
bin/build-searchable-book.sh places (margin30 for lines >= --min-height,
--fix-dashes, --fix-brackets). A pair of katakana words that differ by one
バ行/パ行 kana is a candidate when both forms occur in the book (as in
bin/auto-variant-glossary.py), or when YomiToku (--yomitoku-dir) reads the
other form on the same page. For each pair the page shows a few occurrences,
YomiToku disagreements first, each as a page thumbnail with a red box, the line
around the word and the word at 3x. The reviewer picks the right spelling once
per pair; the page builds a glossary TSV (term, occurrences, variants, verified,
note) to copy into a file and pass to rebuild-text-layer.py --glossary
--fix-variants. Choices are kept in the browser's localStorage only.

usage: bin/build-voicing-review.py PDF OCR_DIR OUT_DIR --ocr-stem STEM
         [--recognized margin.tsv] [--yomitoku-dir DIR] [--title NAME]
"""

import argparse
import csv
import html
import json
import re
from collections import defaultdict
from pathlib import Path

import pypdfium2
from PIL import ImageDraw

from text_layer_utils import fix_brackets, fix_dashes

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
        for item in json.loads(path.read_text())['contents'][0]:
            text = item['text']
            row = recognized.get((page, int(item['id'])))
            if row and int(row['height']) >= args.min_height:
                text = row[args.column]
            vertical = item['isVertical'] == 'true'
            if vertical:
                text = fix_brackets(fix_dashes(text))
            xs = [p[0] for p in item['boundingBox']]
            ys = [p[1] for p in item['boundingBox']]
            lines.append(dict(page=page, box=(min(xs), min(ys), max(xs), max(ys)),
                              vertical=vertical, text=text))
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
        for match in TOKEN.finditer(line['text']):
            if VOICED & set(match.group()):
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
「両方正しい」は別の語として両方を残す。選んだ結果は下のTSVにまとまるので、コピーしてファイルに保存し、
<code>rebuild-text-layer.py --glossary ファイル --fix-variants</code>（または <code>build-searchable-book.sh</code> の3番目の引数）に渡す。
選択はこのブラウザにだけ一時保存される。</p>
<p>{summary}</p>
{sections}
<div id="out"><button id="copy">TSVをコピー</button> <span id="status"></span>
<textarea id="tsv" readonly></textarea></div>
<script>
const PAIRS = {pairs};
const KEY = 'voicing-review:' + document.title;
const SWAP = {{'バ':'パ','ビ':'ピ','ブ':'プ','ベ':'ペ','ボ':'ポ','パ':'バ','ピ':'ビ','プ':'ブ','ペ':'ベ','ポ':'ボ'}};
const variantsOf = w => [...w].flatMap((ch, i) => SWAP[ch] ? [w.slice(0, i) + SWAP[ch] + w.slice(i + 1)] : []);
let saved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function choices() {{
  const c = {{}};
  PAIRS.forEach((p, i) => {{
    const el = document.querySelector('input[name="p' + i + '"]:checked');
    c[i] = el ? el.value : 'undecided';
  }});
  return c;
}}
function render() {{
  const c = choices();
  try {{ localStorage.setItem(KEY, JSON.stringify(c)); }} catch (e) {{}}
  const rows = new Map();
  const add = (term, count, variant, note) => {{
    const r = rows.get(term) || {{term, count, variants: [], notes: []}};
    if (variant) r.variants.push(variant);
    if (note) r.notes.push(note);
    rows.set(term, r);
  }};
  PAIRS.forEach((p, i) => {{
    document.getElementById('s' + i).classList.toggle('done', c[i] !== 'undecided');
    if (c[i] === 'a') add(p.a, p.ca, p.b + '(' + p.cb + '):human');
    if (c[i] === 'b') add(p.b, p.cb, p.a + '(' + p.ca + '):human');
  }});
  // --fix-variants rewrites every one-kana variant of a chosen term unless the
  // variant is itself in the glossary, so a word kept as distinct is listed
  // when it is such a variant.
  const winners = [...rows.keys()];
  const losers = new Set();
  PAIRS.forEach((p, i) => {{ if (c[i] === 'a') losers.add(p.b); if (c[i] === 'b') losers.add(p.a); }});
  PAIRS.forEach((p, i) => {{
    if (c[i] !== 'both') return;
    [[p.a, p.ca], [p.b, p.cb]].forEach(([w, n]) => {{
      if (winners.some(t => t !== w && variantsOf(t).includes(w))) add(w, n, '', 'keep:' + p.a + '/' + p.b);
    }});
  }});
  const lines = ['term\\toccurrences\\tvariants\\tverified\\tnote'];
  rows.forEach(r => lines.push([r.term, r.count, r.variants.join(' '), 'yes', r.notes.join(',')].join('\\t')));
  document.getElementById('tsv').value = lines.join('\\n') + '\\n';
  const n = Object.values(c).filter(v => v !== 'undecided').length;
  const clash = winners.filter(t => losers.has(t));
  document.getElementById('status').textContent = n + ' / ' + PAIRS.length + ' 組を確定'
    + (clash.length ? '。矛盾（正しいと誤りの両方に選んだ語）: ' + clash.join('、') : '');
}}
PAIRS.forEach((p, i) => {{
  const v = saved[i];
  if (v) {{ const el = document.querySelector('input[name="p' + i + '"][value="' + v + '"]'); if (el) el.checked = true; }}
}});
document.addEventListener('change', render);
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
    args = parser.parse_args()

    lines = load_lines(args)
    yomitoku = load_yomitoku(args.yomitoku_dir) if args.yomitoku_dir else {}
    occurrences, pairs = find_pairs(lines, yomitoku, args.min_length)
    order = sorted(pairs, key=lambda k: (-len(pairs[k]['yomi']),
                                         -sum(len(occurrences.get(w, [])) for w in k), k))
    examples = {key: pick_examples(key, pairs[key], occurrences, args.examples) for key in order}

    images = args.output / 'img'
    images.mkdir(parents=True, exist_ok=True)
    by_page = defaultdict(list)
    for n, key in enumerate(order):
        for k, (index, start, end) in enumerate(examples[key]):
            by_page[lines[index]['page']].append((n, k, index, start, end))
    document = pypdfium2.PdfDocument(str(args.pdf))
    for page, items in sorted(by_page.items()):
        image = document[page - 1].render(scale=300 / 72).to_pil().convert('L')
        for n, k, index, start, end in items:
            for kind, picture in zip(('thumb', 'line', 'zoom'), crops(image, lines[index], start, end)):
                picture.save(images / f'{n}-{k}-{kind}.jpg', quality=85)
    document.close()

    sections, data = [], []
    for n, key in enumerate(order):
        a, b = key
        ca, cb = len(occurrences.get(a, [])), len(occurrences.get(b, []))
        data.append({'a': a, 'b': b, 'ca': ca, 'cb': cb})
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
        options = ''.join(f'<label><input type="radio" name="p{n}" value="{value}">{label}</label>'
                          for value, label in (('a', html.escape(a)), ('b', html.escape(b)),
                                               ('both', '両方正しい'), ('undecided', '保留')))
        sections.append(f'<section id="s{n}"><h2>#{n + 1} {html.escape(a)}（{ca}）／{html.escape(b)}（{cb}）'
                        f'</h2><p>YomiTokuと割れた箇所: {len(pairs[key]["yomi"])}</p>'
                        f'<div class="opts">{options}</div><div class="ex">{"".join(figures)}</div></section>')
    summary = (f'{len(order)}組。うちYomiTokuと割れた組 {sum(bool(pairs[k]["yomi"]) for k in order)}。'
               f'本文の行 {len(lines)}、YomiTokuのページ {len(yomitoku)}。')
    (args.output / 'index.html').write_text(PAGE.format(
        title=html.escape(args.title), summary=summary, sections='\n'.join(sections),
        pairs=json.dumps(data, ensure_ascii=False)), encoding='utf-8')
    print(f'{len(order)} pairs, {sum(len(v) for v in examples.values())} examples: {args.output / "index.html"}')


if __name__ == '__main__':
    main()
