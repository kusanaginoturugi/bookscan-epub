#!/usr/bin/env python
"""Let a local LLM settle NDLOCR-Lite / YomiToku disagreements that no rule settles.

Run with .venv-ndl/bin/python. Each NDLOCR-Lite line gets the text
rebuild-text-layer.py would place (margin30 for lines >= --min-height,
--fix-dashes, --fix-brackets, --glossary/--fix-variants) and is paired with
the YomiToku line whose box overlaps it (IoU > 0.5). Character differences
are split into hunks. Hunks that other steps or rules settle are not asked:
バ/パ swaps (voicing review), brackets, ASCII quotes (paired later), dashes, dakuten added or
dropped and small kana (NDL was right on every such case in
work/ocr-engine-check/disagreements-gold.tsv), and text only one engine has.
Lines with many hunks count as misaligned and are skipped. The rest are
asked in both orders as two versions of the line; YomiToku's reading is
applied only when both answers pick it.

OUT_DIR/history.tsv is append-only: one row per hunk asked, with both
answers, model, prompt version and date. A hunk with the same id, model and
prompt is not asked again. OUT_DIR/corrections.tsv lists the hunks to apply
(rebuild-text-layer.py --corrections); verdicts in --human (saved from the
review page) always win over the LLM. The review page lists applied hunks
first, then split answers, then hunks kept as NDL.

usage: bin/judge-ocr-disagreements.py PDF OCR_DIR YOMITOKU_DIR OUT_DIR REVIEW_DIR --ocr-stem STEM
         [--recognized margin.tsv] [--glossary TSV] [--overrides TSV] [--human TSV]
"""

import argparse
import csv
import datetime
import difflib
import html
import importlib.util
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import pypdfium2

from text_layer_utils import (clean_text, fix_brackets, fix_dashes, fix_variants,
                              load_glossary)

ROOT = Path(__file__).resolve().parent
PROMPT_VERSION = 'v1'
PROMPT = """次のA・Bは、小説をOCRで読み取った同じ1行です。数文字だけ読みが違います。
日本語の文章として自然で正しい方を、AかBの1文字だけで答えてください。

A: {a}

B: {b}"""
VOICING = str.maketrans('バビブベボパピプペポ', 'パピプペポバビブベボ')
SMALL = str.maketrans('ァィゥェォッャュョヮヵヶ', 'アイウエオツヤユヨワカケ')
STYLE = {'·': '・', '“': '〝', '”': '〟'}
DASHES = set('―—–─‐-')
CONTEXT = 4
HISTORY = ['id', 'page', 'line_id', 'left', 'ndl', 'yomitoku', 'right', 'line', 'first', 'second',
           'decision', 'model', 'prompt', 'date']
HUMAN = ['id', 'page', 'line_id', 'left', 'from', 'to', 'right', 'verdict']


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def norm_char(ch):
    out = unicodedata.normalize('NFKC', ch)
    out = out if len(out) == 1 else ch
    out = STYLE.get(out, out)
    return '―' if out in DASHES else out


def kind(x, y):
    if '"' in x:
        return 'quote'  # paired into 〝〟 per page after corrections
    if len(x) == len(y) == 1 and x.translate(VOICING) == y:
        return 'voicing'
    if set(x + y) <= set('<>〈〉"〝〟＜＞'):
        return 'bracket'
    if x and set(x) <= {'―'} and (not y or set(y) <= set('ーし|!」')):
        return 'dash'
    if len(x) == len(y) == 1 and unicodedata.normalize('NFKD', x)[0] == unicodedata.normalize('NFKD', y)[0]:
        return 'diacritic'
    if len(x) == len(y) and x.translate(SMALL) == y.translate(SMALL):
        return 'small-kana'
    if not x or not y:
        return 'one-sided'
    return 'judge'


def box_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return w * h / (area(a) + area(b) - w * h)


def load_lines(args, glossary):
    """NDL lines with the text rebuild-text-layer.py places before corrections and quotes."""
    recognized, overridden = {}, set()
    if args.recognized:
        with args.recognized.open(newline='') as handle:
            recognized = {(int(r['page']), int(r['line_id'])): r for r in csv.DictReader(handle, delimiter='\t')}
    if args.overrides:
        with args.overrides.open(newline='') as handle:
            overridden = {(int(r['page']), int(r['line_id'])) for r in csv.DictReader(handle, delimiter='\t')}
    lines = []
    for path in sorted(args.ocr_dir.glob(f'{args.ocr_stem}_*.json')):
        page = int(path.stem.rsplit('_', 1)[1])
        for item in json.loads(path.read_text())['contents'][0]:
            key = (page, int(item['id']))
            if key in overridden:
                continue
            text = item['text']
            row = recognized.get(key)
            if row and int(row['height']) >= args.min_height:
                text = row[args.column]
            vertical = item['isVertical'] == 'true'
            if vertical:
                text = fix_brackets(fix_dashes(text))
            text = clean_text(text, vertical, glossary)
            if glossary:
                text = fix_variants(text, glossary)
            lines.append(dict(page=page, line_id=key[1], box=box_of(item['boundingBox']),
                              vertical=vertical, text=text))
    return lines


def load_yomitoku(directory):
    pages = {}
    for path in directory.glob('*_p*.json'):
        page = int(path.stem.rsplit('_p', 1)[1])
        pages[page] = [(box_of(w['points']), w['content']) for w in json.loads(path.read_text())['words']]
    return pages


def hunks(line, other, max_hunks):
    """Differences between the NDL line text and YomiToku's, as (start, end, from, to, kind)."""
    positions = [i for i, ch in enumerate(line) if not ch.isspace()]
    a = ''.join(norm_char(line[i]) for i in positions)
    b = ''.join(norm_char(ch) for ch in other if not ch.isspace())
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != 'equal']
    if len(ops) > max_hunks or sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops) > 0.4 * max(len(a), 1):
        return None
    out = []
    for _, i1, i2, j1, j2 in ops:
        if i1 < i2:
            start, end = positions[i1], positions[i2 - 1] + 1
        else:
            start = end = positions[i1] if i1 < len(positions) else len(line)
        out.append((start, end, line[start:end], b[j1:j2], kind(a[i1:i2], b[j1:j2])))
    return out


def read_tsv(path):
    if not path or not path.exists():
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle, delimiter='\t'))


PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:15px sans-serif;margin:1.5em;color:#222;background:#fff}}
section{{border-top:2px solid #999;padding:.8em 0}} h2{{font-size:1.1em;margin:.2em 0}}
.applied h2::after{{content:"（反映済み）";color:#c00}} .split h2::after{{content:"（判断が割れた・NDLのまま）";color:#a60}}
.kept h2::after{{content:"（NDLのまま）";color:#666}}
.ex{{display:flex;gap:10px;align-items:flex-start;margin:.5em 0}} .opts label{{margin-right:1em}}
table{{border-collapse:collapse}} td{{padding:2px 8px;border:1px solid #ccc;font-size:16px}}
tr.now td{{background:#e3f4e3;font-weight:bold}}
.done h2{{color:#777}} #out{{position:sticky;bottom:0;background:#f4f4f4;padding:.6em;border-top:2px solid #999}}
textarea{{width:100%;height:10em;font-family:monospace}}
</style>
<h1>{title}</h1>
<p>NDLOCR-LiteとYomiTokuで読みが割れ、ローカルLLM（{model}）が判断した箇所。各箇所の緑の行が、いま文字層に入っている読み。<b>反映済み</b>はYomiTokuの読みに置き換えた箇所。
原本画像と違うものだけ選び直せばよい。正しいと確かめた箇所は、その読みを選ぶと人の確認として記録される。選んだ結果は下のTSVにまとまるので、コピーして <code>{human}</code> に保存し、
<code>judge-ocr-disagreements.py --human</code> に渡して作り直す。人の判定は常にLLMより優先する。選択はこのブラウザにだけ一時保存される。</p>
<p>{summary}</p>
{sections}
<div id="out"><button id="copy">TSVをコピー</button> <span id="status"></span><textarea id="tsv" readonly></textarea></div>
<script>
const ITEMS = {items};
const KEY = 'judge-review:' + document.title;
let saved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
function render() {{
  const out = ['{header}'];
  const store = {{}};
  ITEMS.forEach((it, i) => {{
    const el = document.querySelector('input[name="h' + i + '"]:checked');
    const v = el ? el.value : 'undecided';
    const custom = document.getElementById('c' + i).value;
    store[it.id] = {{v, custom}};
    document.getElementById('s' + i).classList.toggle('done', v !== 'undecided');
    if (v === 'undecided') return;
    const to = v === 'ndl' ? it.from : v === 'yomitoku' ? it.yomitoku : custom;
    out.push([it.id, it.page, it.line_id, it.left, it.from, to, it.right, v].join('\\t'));
  }});
  try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (e) {{}}
  document.getElementById('tsv').value = out.join('\\n') + '\\n';
  document.getElementById('status').textContent = (out.length - 1) + ' / ' + ITEMS.length + ' 件を確定';
}}
ITEMS.forEach((it, i) => {{
  const s = saved[it.id] || it.human;
  if (!s) return;
  const el = document.querySelector('input[name="h' + i + '"][value="' + s.v + '"]');
  if (el) el.checked = true;
  if (s.custom) document.getElementById('c' + i).value = s.custom;
}});
document.addEventListener('change', render);
document.addEventListener('input', render);
document.getElementById('copy').addEventListener('click', async () => {{
  const area = document.getElementById('tsv');
  try {{ await navigator.clipboard.writeText(area.value); }} catch (e) {{ area.select(); document.execCommand('copy'); }}
  document.getElementById('status').textContent += '（コピーした）';
}});
render();
</script>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pdf', type=Path, help='PDF that was given to ocr.py and yomitoku')
    parser.add_argument('ocr_dir', type=Path)
    parser.add_argument('yomitoku_dir', type=Path)
    parser.add_argument('output', type=Path, help='directory for history.tsv and corrections.tsv')
    parser.add_argument('review', type=Path, help='directory for the review page')
    parser.add_argument('--ocr-stem', required=True)
    parser.add_argument('--recognized', type=Path)
    parser.add_argument('--column', default='margin30')
    parser.add_argument('--min-height', type=int, default=400)
    parser.add_argument('--glossary', type=Path, help='glossary used with --fix-variants for the book')
    parser.add_argument('--overrides', type=Path, help='lines replaced by hand are not judged')
    parser.add_argument('--human', type=Path, help='TSV saved from the review page')
    parser.add_argument('--max-hunks', type=int, default=4)
    parser.add_argument('--url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    parser.add_argument('--title', default='LLM判断のレビュー')
    args = parser.parse_args()

    ask = load_module('auto_variant_glossary', ROOT / 'auto-variant-glossary.py').ask
    crops = load_module('voicing_review', ROOT / 'build-voicing-review.py').crops
    glossary = load_glossary(args.glossary) if args.glossary else []
    lines = load_lines(args, glossary)
    yomitoku = load_yomitoku(args.yomitoku_dir)
    args.output.mkdir(parents=True, exist_ok=True)
    history_path = args.output / 'history.tsv'
    done = {(r['id'], r['model'], r['prompt']): dict(r, page=int(r['page']), line_id=int(r['line_id']))
            for r in read_tsv(history_path)}
    human = {r['id']: r for r in read_tsv(args.human)}
    today = datetime.date.today().isoformat()

    items, counts = [], defaultdict(int)
    new_history = []
    for index, line in enumerate(lines):
        words = [text for box, text in yomitoku.get(line['page'], []) if iou(line['box'], box) > 0.5]
        if len(words) != 1:
            counts['unpaired lines'] += 1
            continue
        found = hunks(line['text'], words[0], args.max_hunks)
        if found is None:
            counts['misaligned lines'] += 1
            continue
        for start, end, frm, to, category in found:
            counts[category] += 1
            if category != 'judge':
                continue
            text = line['text']
            left, right = text[max(0, start - CONTEXT):start], text[end:end + CONTEXT]
            hunk_id = f'{line["page"]}:{line["line_id"]}:{left}[{frm}→{to}]{right}'
            record = done.get((hunk_id, args.model, PROMPT_VERSION))
            if record is None:
                alt = text[:start] + to + text[end:]
                first = ask(args.url, args.model, PROMPT.format(a=text, b=alt))
                second = ask(args.url, args.model, PROMPT.format(a=alt, b=text))
                one = {'A': 'ndl', 'B': 'yomitoku'}.get(first)
                two = {'A': 'yomitoku', 'B': 'ndl'}.get(second)
                record = dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left, ndl=frm,
                              yomitoku=to, right=right, line=text, first=first, second=second,
                              decision=one if one and one == two else 'split', model=args.model,
                              prompt=PROMPT_VERSION, date=today)
                new_history.append(record)
                done[(hunk_id, args.model, PROMPT_VERSION)] = record
            items.append(dict(record, index=index, start=start, end=end))

    write_header = not history_path.exists()
    with history_path.open('a', newline='') as handle:
        writer = csv.DictWriter(handle, HISTORY, delimiter='\t', lineterminator='\n', extrasaction='ignore')
        if write_header:
            writer.writeheader()
        writer.writerows(new_history)

    corrections = []
    for item in items:
        verdict = human.get(item['id'])
        if verdict:
            if verdict['to'] != item['ndl']:
                corrections.append((item, verdict['to'], 'human'))
        elif item['decision'] == 'yomitoku':
            corrections.append((item, item['yomitoku'], 'llm'))
    # Hunks closer than their context would overlap when applied; merge them.
    merged = []
    for item, to, source in sorted(corrections, key=lambda c: (c[0]['index'], c[0]['start'])):
        last = merged[-1] if merged else None
        if last and last['index'] == item['index'] and item['start'] - last['end'] < 2 * CONTEXT:
            text = lines[item['index']]['text']
            last.update(to=last['to'] + text[last['end']:item['start']] + to, end=item['end'],
                        id=last['id'] + ' + ' + item['id'], source=','.join(sorted({last['source'], source})))
            continue
        merged.append(dict(index=item['index'], start=item['start'], end=item['end'], to=to,
                           id=item['id'], source=source, page=item['page'], line_id=item['line_id']))
    with (args.output / 'corrections.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['id', 'page', 'line_id', 'left', 'from', 'to', 'right', 'source'])
        for c in merged:
            text = lines[c['index']]['text']
            writer.writerow([c['id'], c['page'], c['line_id'], text[max(0, c['start'] - CONTEXT):c['start']],
                             text[c['start']:c['end']], c['to'], text[c['end']:c['end'] + CONTEXT], c['source']])

    order = {'yomitoku': 0, 'split': 1, 'ndl': 2}
    items.sort(key=lambda it: (order[it['decision']], it['page'], it['line_id']))
    images = args.review / 'img'
    images.mkdir(parents=True, exist_ok=True)
    document = pypdfium2.PdfDocument(str(args.pdf))
    by_page = defaultdict(list)
    for n, item in enumerate(items):
        by_page[item['page']].append((n, item))
    for page, entries in sorted(by_page.items()):
        image = document[page - 1].render(scale=300 / 72).to_pil().convert('L')
        for n, item in entries:
            for name, picture in zip(('thumb', 'line', 'zoom'),
                                     crops(image, lines[item['index']], item['start'], max(item['end'], item['start'] + 1))):
                picture.save(images / f'{n}-{name}.jpg', quality=85)
    document.close()

    sections, data = [], []
    labels = {'yomitoku': 'applied', 'split': 'split', 'ndl': 'kept'}
    for n, item in enumerate(items):
        verdict = human.get(item['id'])
        data.append({'id': item['id'], 'page': item['page'], 'line_id': item['line_id'], 'left': item['left'],
                     'from': item['ndl'], 'yomitoku': item['yomitoku'], 'right': item['right'],
                     'human': {'v': verdict['verdict'], 'custom': verdict['to']} if verdict else None})
        esc = html.escape
        # Prompt order: first asks A=NDL, B=YomiToku; second swaps them.
        said = lambda answer, a, b: {'A': a, 'B': b}.get(answer, '答えなし')
        first = said(item['first'], 'NDL', 'YomiToku')
        second = said(item['second'], 'YomiToku', 'NDL')
        reading = {'NDL': item['ndl'], 'YomiToku': item['yomitoku']}
        llm = (f'1回目: {esc(reading.get(first, ""))}（{first}）／'
               f'2回目（順序を入れ替え）: {esc(reading.get(second, ""))}（{second}）')
        if verdict:
            now, why = verdict['to'], '人の判定'
        elif item['decision'] == 'yomitoku':
            now, why = item['yomitoku'], 'YomiTokuの読み。LLMが2回とも選んだ'
        elif item['decision'] == 'split':
            now, why = item['ndl'], 'NDLの読み。LLMの答えが割れた'
        else:
            now, why = item['ndl'], 'NDLの読み。LLMが2回とも選んだ'
        current = 'yomitoku' if item['decision'] == 'yomitoku' else 'ndl'
        mark = lambda v: '（反映中）' if v == current and not verdict else ''
        options = ''.join(f'<label><input type="radio" name="h{n}" value="{v}">{label}</label>'
                          for v, label in (('ndl', f'NDL「{esc(item["ndl"])}」{mark("ndl")}'),
                                           ('yomitoku', f'YomiToku「{esc(item["yomitoku"])}」{mark("yomitoku")}'),
                                           ('custom', '自分で書く'), ('undecided', 'LLMの判断のまま')))
        sections.append(
            f'<section id="s{n}" class="{labels[item["decision"]]}"><h2>#{n + 1} {item["page"]}ページ・行{item["line_id"]}</h2>'
            f'<table><tr class="now"><td>文字層の今の読み</td><td>{esc(item["left"])}{esc(now)}{esc(item["right"])}（{why}）</td></tr>'
            f'<tr><td>NDL</td><td>{esc(item["left"])}<b>{esc(item["ndl"])}</b>{esc(item["right"])}</td></tr>'
            f'<tr><td>YomiToku</td><td>{esc(item["left"])}<b>{esc(item["yomitoku"])}</b>{esc(item["right"])}</td></tr>'
            f'<tr><td>LLM</td><td>{llm}</td></tr></table>'
            f'<div class="ex"><img src="img/{n}-thumb.jpg"><img src="img/{n}-line.jpg"><img src="img/{n}-zoom.jpg"></div>'
            f'<div class="opts">{options} <input id="c{n}" size="12" placeholder="自分で書く文字列"></div></section>')
    summary = (f'判断した{len(items)}か所: 反映 {sum(i["decision"] == "yomitoku" for i in items)}、'
               f'割れた {sum(i["decision"] == "split" for i in items)}、NDLのまま {sum(i["decision"] == "ndl" for i in items)}。'
               f'人の判定 {sum(1 for i in items if i["id"] in human)}。規則で除外した差分: '
               + '、'.join(f'{k} {v}' for k, v in sorted(counts.items()) if k != 'judge'))
    (args.review / 'index.html').write_text(PAGE.format(
        title=html.escape(args.title), model=html.escape(args.model),
        human=html.escape(str(args.human or 'work/eval/作業名-judge-human.tsv')), summary=summary,
        sections='\n'.join(sections), items=json.dumps(data, ensure_ascii=False), header='\\t'.join(HUMAN)),
        encoding='utf-8')
    print(summary)
    print(f'{len(new_history)} hunks asked now, {len(merged)} corrections: {args.output / "corrections.tsv"}')


if __name__ == '__main__':
    main()
