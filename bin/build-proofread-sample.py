#!/usr/bin/env python3
"""Proofread a random sample of lines and score each stage of the text layer.

Without a full correct text there is no success rate, but a random sample is
enough to estimate one: a person picks, for each sampled line, the reading
that matches the page image (or types it), and --score reports the character
error rate of every stage on those lines (raw NDL, margin re-read, YomiToku,
rules, LLM/human corrections, final) with a bootstrap 95% interval.

The stages come from rebuild-text-layer.py --dump-lines (work/searchable/SLUG/lines.tsv).
"""

import argparse
import csv
import difflib
import html
import importlib.util
import json
import random
import re
import shlex
import statistics
import sys
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from text_layer_utils import catalog_pages, parse_pages  # noqa: E402

STAGES = [('raw', 'NDLの生の読み'), ('margin', 'NDL（余白つき再認識）'), ('yomitoku', 'YomiToku'),
          ('rules', '規則で補正'), ('judged', '判断モデル・人の判定'), ('final', '完成（引用符の組み直しなど）')]
FONT = 22  # px per character in the page; line images are scaled to the same pitch


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}


def bare(text):
    # Spaces are not counted, and full-width ASCII (？, Ａ) counts as half-width:
    # the width is a mechanical choice for the ePub stage, not an OCR error.
    return re.sub(r'\s', '', text).translate(WIDTH)


def read_tsv(path):
    if not path or not path.exists():
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle, delimiter='\t'))


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def marked(text, against, cls):
    # Characters of text that differ from against, wrapped for highlighting.
    out = []
    for op, i1, i2, _, _ in difflib.SequenceMatcher(None, text, against, autojunk=False).get_opcodes():
        chunk = html.escape(text[i1:i2])
        out.append(chunk if op == 'equal' or not chunk else f'<span class="{cls}">{chunk}</span>')
    return ''.join(out)


def sample(args):
    judge = load_module('judge', ROOT / 'judge-ocr-disagreements.py')
    rows = [r for r in read_tsv(args.lines) if r['vertical'] == '1']
    skip = parse_pages(args.skip_pages) | (catalog_pages(args.ocr_dir, args.ocr_stem) if args.ocr_dir else set())
    for row in rows:
        row['box'] = tuple(float(v) for v in row['box'].split(','))
    widths = statistics.median(r['box'][2] - r['box'][0] for r in rows)
    # Body lines only: ruby is narrow, and very short lines say little.
    eligible = [r for r in rows if int(r['page']) not in skip and len(bare(r['final'])) >= 4
                and r['box'][2] - r['box'][0] >= 0.6 * widths]
    picked = sorted(random.Random(args.seed).sample(eligible, min(args.count, len(eligible))),
                    key=lambda r: (int(r['page']), int(r['line_id'])))
    yomitoku = judge.load_yomitoku(args.yomitoku_dir)
    for row in picked:
        words = [text for box, text in yomitoku.get(int(row['page']), []) if judge.iou(row['box'], box) > 0.5]
        row['yomitoku'] = words[0] if len(words) == 1 else ''
        row['id'] = f'{row["page"]}:{row["line_id"]}'
    return picked, len(eligible)


def build(args):
    picked, eligible = sample(args)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'sample.tsv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, ['id', 'page', 'line_id'] + [s for s, _ in STAGES], delimiter='\t',
                                lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(picked)
    images = args.output / 'img'
    images.mkdir(exist_ok=True)
    document = pypdfium2.PdfDocument(str(args.pdf))
    rendered = {}
    for n, row in enumerate(picked):
        page = int(row['page'])
        if page not in rendered:
            rendered = {page: document[page - 1].render(scale=300 / 72).to_pil().convert('L')}
        image = rendered[page]
        x0, y0, x1, y1 = row['box']
        per = (y1 - y0) / max(len(bare(row['final'])), 1)
        crop = image.crop((int(max(0, x0 - 10)), int(max(0, y0 - per * 0.3)),
                           int(min(image.width, x1 + 10)), int(min(image.height, y1 + per * 0.3))))
        scale = FONT / per
        crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))).save(
            images / f'{n}.jpg', quality=88)
    document.close()

    human = {r['id']: r for r in read_tsv(args.human)}
    items, sections = [], []
    for n, row in enumerate(picked):
        final = bare(row['final'])
        candidates = {}
        for stage, _ in (('final', 0), ('yomitoku', 0), ('margin', 0), ('raw', 0)):
            text = bare(row[stage])
            if text:
                candidates.setdefault(text, []).append(stage)
        columns = []
        for k, (text, sources) in enumerate(candidates.items()):
            body = marked(text, bare(row['raw']), 'chg') if k == 0 else marked(text, final, 'diff')
            columns.append(f'<button class="cand" data-n="{n}" data-k="{k}"><span class="v">{body}</span></button>')
        items.append({'id': row['id'], 'page': int(row['page']), 'line_id': int(row['line_id']),
                      'cands': [[t, ','.join(s)] for t, s in candidates.items()],
                      'human': human.get(row['id'], {}).get('truth'),
                      'skip': human.get(row['id'], {}).get('choice') == 'skip'})
        sections.append(
            f'<section id="s{n}"><div class="head"><b>#{n + 1}</b><br>{row["page"]}ページ<br>行{row["line_id"]}'
            f'<br><span class="state" id="st{n}"></span></div>'
            f'<img class="line" src="img/{n}.jpg" alt="原本の行">{"".join(columns)}'
            f'<div class="more"><button class="other" data-n="{n}">どれも違う（書く）</button>'
            f'<input class="custom" id="c{n}" value="{html.escape(final)}" hidden>'
            f'<button class="skip" data-n="{n}">除外（行の切り出しが変・読めない）</button></div></section>')
    save_path = json.dumps(shlex.quote(str(args.human.resolve() if args.human else 'proofread-human.tsv')))
    page = PAGE.format(title=html.escape(args.title), count=len(picked), eligible=eligible, seed=args.seed,
                       human=html.escape(str(args.human.resolve() if args.human else 'proofread-human.tsv')),
                       sections='\n'.join(sections), items=json.dumps(items, ensure_ascii=False),
                       save_path=save_path, font=FONT)
    (args.output / 'index.html').write_text(page, encoding='utf-8')
    print(f'{len(picked)} lines sampled from {eligible} body lines (seed {args.seed}); '
          f'review: {(args.output / "index.html").resolve()} -> save as {args.human}')


def score(args):
    rows = {r['id']: r for r in read_tsv(args.output / 'sample.tsv')}
    truth = {r['id']: r['truth'] for r in read_tsv(args.human) if r['choice'] != 'skip' and r['id'] in rows}
    skipped = sum(r['choice'] == 'skip' for r in read_tsv(args.human))
    print(f'{len(truth)} proofread lines ({sum(len(t) for t in truth.values())} characters), '
          f'{skipped} excluded, {len(rows) - len(truth) - skipped} not yet proofread')
    ids = sorted(truth)
    series = [(label, {i: rows[i][stage] for i in ids}) for stage, label in STAGES]
    if args.machine_lines:
        # The same pipeline rebuilt without any human verdict or choice.
        machine = {f'{r["page"]}:{r["line_id"]}': r for r in read_tsv(args.machine_lines)}
        for stage, label in STAGES:
            if stage in ('rules', 'judged', 'final'):
                series.append((f'人の判定なし: {label}', {i: machine[i][stage] for i in ids}))
    rng = random.Random(0)
    print('stage\tlines\terrors\tchars\tcharacters right\tlines right\tper 10,000 chars (95%)')
    for label, texts in series:
        per_line = [(distance(bare(texts[i]), truth[i]), len(truth[i])) for i in ids if texts[i]]
        if not per_line:
            continue
        errors, chars = sum(e for e, _ in per_line), sum(c for _, c in per_line)
        rates = []
        for _ in range(2000):
            pick = [per_line[rng.randrange(len(per_line))] for _ in per_line]
            rates.append(sum(e for e, _ in pick) / max(sum(c for _, c in pick), 1) * 10000)
        rates.sort()
        # With no errors the resampled interval collapses to 0; give the rule-of-three
        # upper bound (95%: fewer than 3 / n) instead.
        interval = (f'{rates[50]:.1f}–{rates[1949]:.1f}' if errors else
                    f'0–{3 / chars * 10000:.1f}、行では{len(per_line)}行中0で上限{3 / len(per_line):.1%}')
        right = sum(e == 0 for e, _ in per_line)
        print(f'{label}\t{len(per_line)}\t{errors}\t{chars}\t{1 - errors / chars:.2%}\t'
              f'{right}/{len(per_line)} ({right / len(per_line):.1%})\t{errors / chars * 10000:.1f} ({interval})')
    for label, texts in [s for s in series if s[0].endswith(STAGES[-1][1])]:
        wrong = [i for i in ids if bare(texts[i]) != truth[i]]
        if wrong:
            print(f'\nlines {label} still gets wrong:')
            for i in wrong:
                print(f'  {i}\t{bare(texts[i])}\n  {"":{len(i)}}\t{truth[i]}')


PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:15px sans-serif;margin:1.5em;color:#222;background:#fff;padding-bottom:6em}}
section{{display:flex;gap:10px;align-items:flex-start;border-top:2px solid #999;padding:.8em 0}}
.head{{width:7em;flex:none}} img.line{{border:1px solid #bbb;flex:none}}
.cand{{writing-mode:vertical-rl;font:{font}px/1 "Noto Serif CJK JP","Noto Serif JP",serif;padding:4px 6px;
  background:#fff;border:2px solid #ccc;border-radius:6px;cursor:pointer;flex:none;text-align:start}}
.cand:hover{{border-color:#36c}} .cand.on{{border-color:#1a7f37;background:#e6f4ea}}
.diff{{color:#c00;background:#fde2e2}} .chg{{background:#dbe9ff}}
.more{{display:flex;flex-direction:column;gap:.5em}} .more input{{font-size:{font}px;width:24em}}
.on.other,.on.skip{{background:#e6f4ea;border-color:#1a7f37}}
section.done .head{{color:#1a7f37}} section.skipped .head{{color:#999}}
#out{{position:fixed;left:0;right:0;bottom:0;background:#f4f4f4;padding:.6em 1.5em;border-top:2px solid #999}}
#tsv{{display:none}}
</style>
<h1>{title}</h1>
<p>本文から無作為に選んだ{count}行（本文の行 {eligible} から、乱数の種 {seed}）。左が原本の行の画像で、右に読みの候補を縦書きで並べた（文字の大きさを画像にそろえてある）。
<b>原本と1字残らず同じ候補をクリック</b>すると、次の行へ進む。<span class="chg">青</span>は完成版がNDLの生の読みから直した字、<span class="diff">赤</span>はほかの候補が完成版と違う字。
どれも違えば「どれも違う」で正しい行を書く（Enterで確定）。行の切り出しがおかしいときは「除外」。ルビ、空白、行をまたぐ語は気にしなくていい（空白は数えない）。</p>
<p>終わったら下の「保存コマンドをコピー」を押して端末に貼る（{human} に保存される）。選択はこのブラウザにも一時保存される。キー: n で次の未確認、p で前。</p>
{sections}
<div id="out"><b id="progress"></b>　<button id="next">次の未確認へ ↓</button>
<button id="savecmd">保存コマンドをコピー</button> <button id="copy">TSVだけコピー</button> <span id="status"></span>
<textarea id="tsv"></textarea></div>
<script>
const ITEMS = {items};
const SAVE_PATH = {save_path};
const KEY = 'proofread:' + document.title;
let state = {{}};
try {{ state = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}
ITEMS.forEach((it, n) => {{
  if (state[n] === undefined && it.skip) state[n] = {{choice: 'skip', truth: ''}};
  else if (state[n] === undefined && it.human != null) {{
    const k = it.cands.findIndex(c => c[0] === it.human);
    state[n] = k >= 0 ? {{choice: it.cands[k][1], truth: it.human, k}} : {{choice: 'custom', truth: it.human}};
  }}
}});
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(state)); }} catch (e) {{}} }}
function render() {{
  let done = 0;
  ITEMS.forEach((it, n) => {{
    const s = state[n], sec = document.getElementById('s' + n);
    sec.querySelectorAll('.cand').forEach(b => b.classList.toggle('on', !!s && s.k === +b.dataset.k));
    sec.querySelector('.other').classList.toggle('on', !!s && s.choice === 'custom');
    sec.querySelector('.skip').classList.toggle('on', !!s && s.choice === 'skip');
    const input = document.getElementById('c' + n);
    input.hidden = !(s && s.choice === 'custom');
    if (s && s.choice === 'custom' && document.activeElement !== input) input.value = s.truth;
    sec.classList.toggle('done', !!s && s.choice !== 'skip');
    sec.classList.toggle('skipped', !!s && s.choice === 'skip');
    document.getElementById('st' + n).textContent = !s ? '' : s.choice === 'skip' ? '除外' : '済み';
    if (s) done++;
  }});
  document.getElementById('progress').textContent = `${{done}} / ${{ITEMS.length}} 行`;
  const rows = ['id\\tpage\\tline_id\\tchoice\\ttruth'];
  ITEMS.forEach((it, n) => {{ const s = state[n]; if (s) rows.push([it.id, it.page, it.line_id, s.choice, s.truth].join('\\t')); }});
  document.getElementById('tsv').value = rows.join('\\n') + '\\n';
}}
function next(from, dir = 1) {{
  for (let i = from + dir; i >= 0 && i < ITEMS.length; i += dir)
    if (!state[i]) {{ document.getElementById('s' + i).scrollIntoView({{block: 'start'}}); return i; }}
  return from;
}}
let current = -1;
document.addEventListener('click', e => {{
  const b = e.target.closest('button');
  if (!b || b.dataset.n === undefined) return;
  const n = +b.dataset.n; current = n;
  if (b.classList.contains('cand')) {{
    const c = ITEMS[n].cands[+b.dataset.k];
    state[n] = {{choice: c[1], truth: c[0], k: +b.dataset.k}}; save(); render(); next(n);
  }} else if (b.classList.contains('skip')) {{
    state[n] = {{choice: 'skip', truth: ''}}; save(); render(); next(n);
  }} else if (b.classList.contains('other')) {{
    const input = document.getElementById('c' + n);
    state[n] = {{choice: 'custom', truth: input.value.replace(/\\s/g, '')}}; save(); render();
    input.hidden = false; input.focus();
  }}
}});
document.querySelectorAll('.custom').forEach(input => {{
  const n = +input.id.slice(1);
  input.addEventListener('input', () => {{ state[n] = {{choice: 'custom', truth: input.value.replace(/\\s/g, '')}}; save(); render(); }});
  input.addEventListener('keydown', e => {{ if (e.key === 'Enter') {{ input.blur(); next(n); }} }});
}});
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'n') current = next(current);
  if (e.key === 'p') current = next(current, -1);
}});
document.getElementById('next').addEventListener('click', () => {{ current = next(current); }});
document.getElementById('savecmd').addEventListener('click', async () => {{
  // One click: a shell command with the TSV inside (base64, so tabs and
  // Japanese survive the terminal paste) that writes the file.
  const tsv = document.getElementById('tsv').value;
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(tsv)));
  await navigator.clipboard.writeText("echo '" + b64 + "' | base64 -d > " + SAVE_PATH);
  document.getElementById('status').textContent = '保存コマンドをコピーした。端末に貼って実行';
}});
document.getElementById('copy').addEventListener('click', async () => {{
  await navigator.clipboard.writeText(document.getElementById('tsv').value);
  document.getElementById('status').textContent = 'TSVをコピーした';
}});
render();
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pdf', type=Path, help='original PDF (the OCR boxes are in its 300 dpi pixels)')
    parser.add_argument('lines', type=Path, help='TSV from rebuild-text-layer.py --dump-lines')
    parser.add_argument('yomitoku_dir', type=Path)
    parser.add_argument('output', type=Path, help='review directory (index.html, sample.tsv, img/)')
    parser.add_argument('--human', type=Path, help='TSV saved from the page (id, page, line_id, choice, truth)')
    parser.add_argument('--count', type=int, default=200)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--ocr-dir', type=Path, help='with --ocr-stem, leave out back-matter catalog pages')
    parser.add_argument('--ocr-stem')
    parser.add_argument('--skip-pages', help="pages left out, e.g. '1-6,312-322'")
    parser.add_argument('--machine-lines', type=Path,
                        help='with --score, also score a --dump-lines TSV of the same book rebuilt without '
                             'human verdicts (machine processing only)')
    parser.add_argument('--score', action='store_true', help='score the stages against --human instead of building')
    parser.add_argument('--title', default='抜き取り検査')
    args = parser.parse_args()
    if args.score:
        if not args.human:
            parser.error('--score needs --human')
        score(args)
    else:
        build(args)


if __name__ == '__main__':
    main()
