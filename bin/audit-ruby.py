#!/usr/bin/env python3
"""Audit extracted ruby.tsv entries with a local LLM (or reuse an existing audit TSV)
and build an interactive review HTML with original 2x crops and one-click save command.
"""

import argparse
import base64
import csv
import html
import io
import json
import re
import shlex
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin'))
from text_layer_utils import normalize_ruby  # noqa: E402

HUMAN_COLS = ['id', 'page', 'line_id', 'choice', 'base', 'ruby']
HIRAGANA_RE = re.compile(r'[ぁ-ゖ]')
HIRAGANA_RUBY = re.compile(r'^[ぁ-ゖー]+$')

PROMPT = """日本語小説のルビ（振り仮名）の検算です。
小説のルビには「難読漢字の読み」だけでなく、「一般的な読みとは異なる表外訓・熟字訓・文語読み・当て字（例: 穿→は、傾→かし、解→ほど、詰→なじ、敵→かな、刷→は、猛者→もさ、手袋→ガントレツト、微子→ニユートリノ）」も多く使われます。
本文スニペット中の親文字「{base}」に、ルビ「{ruby}」が振られています（元のルビOCRは「{raw}」）。

判定ルール:
- 親文字「{base}」に対する読みとして「{ruby}」が、難読漢字の読み・表外訓・熟字訓・文語読み・当て字のいずれかとしてこの文脈で成立すれば OK とだけ答える。
- 特に、現在のルビが元ルビOCRと一致しており、その漢字・文脈の特殊な読みや当て字としてあり得るなら、一般的な読みに書き直さず必ず OK にする。
- ルビはひらがな・カタカナとも小書き仮名（ぁぃぅぇぉっゃゅょゎ・ァィゥェォッャュョヮヵヶ）を使わず、すべて大きい仮名（あいうえおつやゆよわ・アイウエオツヤユヨワカケ）で統一する。FIX を返すときもルビはすべて大きい仮名で書く（例: フィギュア→フイギユア、きょうく→きようく）。
- FIX にするのは次の場合に限る: (1) 親文字の範囲ズレ・過不足（熟語の一部だけに切れている、隣の字・送り仮名・助詞を含んでいる等。例: 潜り抜→くぐ は 潜→くぐ、粘り強→ねば は 粘→ねば、機を窺→うかが は 窺→うかが）、(2) 元OCRの濁点・1文字誤認が残っている、(3) 読みが文脈と明らかに無関係・誤り（例: 飽→あつ、湧→わか）。
  その場合は
  FIX\t正しい親文字\t正しいルビ
  の1行で答える（正しい親文字は本文スニペット内の連続した文字列。送り仮名は含めない）。
- ルビOCRがゴミで本来ルビ不要なら DROP とだけ答える。
- 説明は不要。OK / DROP / FIX\t...\t... のいずれか1行のみ出力せよ。

本文スニペット: {snippet}
親文字: {base}
ルビ: {ruby}
元ルビOCR: {raw}"""


def norm_pair(b, rb):
    if HIRAGANA_RUBY.fullmatch(rb):
        while len(b) >= 2 and ('\u3041' <= b[0] <= '\u3096') and not rb.startswith(b[0]):
            b = b[1:]
    trimmed_tail = False
    while (len(b) >= 2 and len(rb) >= 2 and ('\u3041' <= b[-1] <= '\u3096') and
           (b[-1] == rb[-1] or (trimmed_tail and b[-1] == 'っ' and rb[-1] == 'つ'))):
        b, rb = b[:-1], rb[:-1]
        trimmed_tail = True
    while len(b) >= 2 and ('\u3041' <= b[-1] <= '\u3096') and not rb.endswith(b[-1]):
        b = b[:-1]
    return b, normalize_ruby(rb)


def suspicious_reason(b, y0, y1, per):
    """Flag mechanical base/span mismatches even when LLM says OK."""
    if HIRAGANA_RE.search(b):
        return 'hiragana-in-base'
    h = y1 - y0
    if len(b) >= 2 and not b.isascii() and h < per * len(b) * 0.55:
        return f'short-span({h}px/{len(b)}ch)'
    return ''


def ask_audit(url, model, snippet, base, ruby, raw):
    body = {
        'model': model,
        'temperature': 0,
        'max_tokens': 32,
        'chat_template_kwargs': {'enable_thinking': False},
        'messages': [{'role': 'user', 'content': PROMPT.format(
            snippet=snippet, base=base, ruby=ruby, raw=raw)}],
    }
    req = urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode(),
                                 {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = json.load(resp)['choices'][0]['message']['content'].strip()
    return text.splitlines()[0].strip() if text else 'OK'


PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:15px/1.5 sans-serif;max-width:980px;margin:1.5em auto;padding:0 1em 7em;color:#222}}
section{{display:flex;gap:16px;border-top:2px solid #999;padding:.8em 0;align-items:flex-start}}
section.done{{opacity:.55}} section.done h2{{color:#1a7f37}}
.img img{{border:1px solid #bbb;max-height:220px;display:block}}
.info{{flex:1}} h2{{margin:0 0 .3em;font-size:1.05em}} p{{margin:.25em 0}}
.fix{{color:#047857;font-weight:bold}} .drop{{color:#b91c1c;font-weight:bold}}
.snip{{color:#555;font-size:.92em}}
.opts{{margin-top:.5em;display:flex;flex-wrap:wrap;gap:.8em;align-items:center}}
.opts label{{cursor:pointer;padding:2px 6px;border-radius:4px;border:1px solid #ddd;background:#f9f9f9}}
.opts input[type=text]{{font-size:15px;padding:2px 6px}}
#out{{position:fixed;left:0;right:0;bottom:0;background:#f4f4f4;padding:.6em 1.5em;border-top:2px solid #999;display:flex;flex-wrap:wrap;gap:.6em;align-items:center}}
#tsv{{display:none}}
</style>
<h1>{title}（{count}件）</h1>
<p>原本の2倍拡大画像を見て、ルビの正誤を選ぶ。同じ <code>(親文字, ルビ)</code> の別箇所にも自動で同じ選択が入る。ルビの小書き仮名は保存時に自動で大きい仮名に統一される。キー: <b>n</b> 次の未確認 / <b>p</b> 前。
終わったら下の「保存コマンドをコピー」を押して端末に貼ると <code>{human}</code> に保存される。</p>
{sections}
<div id="out">
  <button id="prev">↑ 前（p）</button>
  <button id="next">次の未確認 ↓（n）</button>
  <label><input type="checkbox" id="advance" checked>選んだら次へ</label>
  <button id="savecmd">保存コマンドをコピー</button>
  <button id="copy">TSVだけコピー</button>
  <span id="status"></span>
  <textarea id="tsv"></textarea>
</div>
<script>
const ITEMS = {items};
const PASSTHROUGH = {passthrough};
const SAVE_PATH = {save_path};
const KEY = 'ruby-review:' + document.title;
const SMALL_FROM = 'ァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖ';
const SMALL_TO   = 'アイウエオツヤユヨワカケあいうえおつやゆよわかけ';
function normRuby(s) {{
  return s ? s.replace(/[ァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖ]/g, c => SMALL_TO[SMALL_FROM.indexOf(c)]) : s;
}}
let saved = {{}};
const autoCustom = {{}};
try {{ saved = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{}}

function render() {{
  const out = ['{header}'];
  const store = {{}};
  let done = 0;
  ITEMS.forEach((it, i) => {{
    const el = document.querySelector('input[name="r' + i + '"]:checked');
    const v = el ? el.value : 'undecided';
    const cb = document.getElementById('cb' + i).value.trim();
    const cr = normRuby(document.getElementById('cr' + i).value.trim());
    document.getElementById('s' + i).classList.toggle('done', v !== 'undecided');
    if (v === 'undecided') return;
    done++;
    store[it.id] = {{v, cb, cr}};
    let b = it.base, rb = it.ruby;
    if (v === 'fix') {{ b = it.new_base; rb = it.new_ruby; }}
    else if (v === 'drop') {{ b = ''; rb = ''; }}
    else if (v === 'custom') {{ b = cb; rb = cr; }}
    out.push([it.id, it.page, it.line_id, v, b, normRuby(rb)].join('\\t'));
  }});
  PASSTHROUGH.forEach(r => {{
    out.push([r.id, r.page, r.line_id, r.choice, r.base, normRuby(r.ruby)].join('\\t'));
  }});
  try {{ localStorage.setItem(KEY, JSON.stringify(store)); }} catch (e) {{}}
  document.getElementById('tsv').value = out.join('\\n') + '\\n';
  document.getElementById('status').textContent = done + ' / ' + ITEMS.length + ' 件確定';
}}

ITEMS.forEach((it, i) => {{
  const s = saved[it.id] || it.human;
  if (!s) return;
  const el = document.querySelector('input[name="r' + i + '"][value="' + s.v + '"]');
  if (el) el.checked = true;
  if (s.v === 'custom') {{
    if (s.cb !== undefined) document.getElementById('cb' + i).value = s.cb;
    if (s.cr !== undefined) document.getElementById('cr' + i).value = normRuby(s.cr);
  }}
}});

function isOpen(i) {{
  const el = document.querySelector('input[name="r' + i + '"]:checked');
  return !el || el.value === 'undecided';
}}
function jump(from, dir) {{
  for (let i = from + dir; i >= 0 && i < ITEMS.length; i += dir) {{
    if (isOpen(i)) {{ document.getElementById('s' + i).scrollIntoView({{behavior: 'smooth', block: 'start'}}); return; }}
  }}
  document.getElementById('status').textContent += (dir > 0 ? '（この先に未確認なし）' : '（この前に未確認なし）');
}}
function current() {{
  let at = -1;
  for (let i = 0; i < ITEMS.length; i++) {{
    if (document.getElementById('s' + i).getBoundingClientRect().top <= 16) at = i; else break;
  }}
  return at;
}}

document.addEventListener('change', e => {{
  const m = e.target.name && e.target.name.match(/^r(\\d+)$/);
  if (m) {{
    const i = +m[1];
    const v = e.target.value;
    delete autoCustom[i];
    // Propagate identical (base, ruby) pair choices to unanswered items (defer custom until edited)
    if (v !== 'undecided' && v !== 'custom') {{
      ITEMS.forEach((it, j) => {{
        if (j === i || it.sig !== ITEMS[i].sig || !isOpen(j)) return;
        const el = document.querySelector('input[name="r' + j + '"][value="' + v + '"]');
        if (el) el.checked = true;
      }});
    }}
    render();
    if (v !== 'undecided' && v !== 'custom' && document.getElementById('advance').checked) {{
      setTimeout(() => jump(i, 1), 120);
    }}
  }} else {{
    render();
  }}
}});
document.addEventListener('input', e => {{
  const m = e.target.id && e.target.id.match(/^c[br](\\d+)$/);
  if (m) {{
    const i = +m[1];
    delete autoCustom[i];
    const el = document.querySelector('input[name="r' + i + '"][value="custom"]');
    if (el) el.checked = true;
    const cb = document.getElementById('cb' + i).value;
    const cr = document.getElementById('cr' + i).value;
    ITEMS.forEach((it, j) => {{
      if (j === i || it.sig !== ITEMS[i].sig) return;
      if (!isOpen(j) && autoCustom[j] !== i) return;
      const cel = document.querySelector('input[name="r' + j + '"][value="custom"]');
      if (cel) {{
        cel.checked = true;
        document.getElementById('cb' + j).value = cb;
        document.getElementById('cr' + j).value = cr;
        autoCustom[j] = i;
      }}
    }});
  }}
  render();
}});
document.querySelectorAll('button.upto').forEach(b => b.addEventListener('click', () => {{
  for (let i = 0; i <= +b.dataset.n; i++) {{
    if (isOpen(i)) document.querySelector('input[name="r' + i + '"][value="keep"]').checked = true;
  }}
  render();
}}));
document.getElementById('next').addEventListener('click', () => jump(current(), 1));
document.getElementById('prev').addEventListener('click', () => jump(Math.max(current(), 0), -1));
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' && e.target.type === 'text') return;
  if (e.key === 'n') jump(current(), 1);
  if (e.key === 'p') jump(Math.max(current(), 0), -1);
}});
document.getElementById('savecmd').addEventListener('click', async () => {{
  const tsv = document.getElementById('tsv').value;
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(tsv)));
  const cmd = "echo '" + b64 + "' | base64 -d > " + SAVE_PATH;
  try {{ await navigator.clipboard.writeText(cmd); }}
  catch (e) {{ const a = document.getElementById('tsv'); a.style.display = 'block'; a.value = cmd; a.select(); document.execCommand('copy'); }}
  document.getElementById('status').textContent += '（保存コマンドをコピーした）';
}});
document.getElementById('copy').addEventListener('click', async () => {{
  try {{ await navigator.clipboard.writeText(document.getElementById('tsv').value); }} catch (e) {{}}
  document.getElementById('status').textContent += '（TSVをコピーした）';
}});
render();
</script>
</html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work_dir', type=Path, help='work/searchable/SLUG')
    parser.add_argument('ruby_tsv', type=Path, help='work/epub/SLUG/ruby.tsv')
    parser.add_argument('audit_tsv', type=Path, help='output (or input with --from-audit) audit TSV')
    parser.add_argument('review_html', type=Path, help='output interactive review HTML')
    parser.add_argument('--source', required=True, help='source PDF filename')
    parser.add_argument('--human', type=Path, help='path to save/load ruby-human.tsv')
    parser.add_argument('--from-audit', action='store_true', help='skip LLM and rebuild HTML from existing audit_tsv')
    parser.add_argument('--title', default='ルビ検算レビュー')
    parser.add_argument('--url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    args = parser.parse_args()

    lines_box = {}
    lines_per = {}
    with (args.work_dir / 'lines.tsv').open(newline='') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            key = (int(r['page']), int(r['line_id']))
            box = tuple(float(v) for v in r['box'].split(','))
            lines_box[key] = box
            t = r.get('final', '').strip()
            if r.get('vertical') == '1' and t:
                lines_per[key] = (box[3] - box[1]) / len(t)

    if args.from_audit and args.audit_tsv.exists():
        flagged = list(csv.DictReader(args.audit_tsv.open(newline=''), delimiter='\t'))
        for it in flagged:
            it['ruby'] = normalize_ruby(it.get('ruby', ''))
            it['new_ruby'] = normalize_ruby(it.get('new_ruby', ''))
    else:
        rows = list(csv.DictReader(args.ruby_tsv.open(newline=''), delimiter='\t'))
        groups = defaultdict(list)
        for idx, r in enumerate(rows):
            b, rb = r.get('base', ''), r.get('ruby', '')
            if not b or not rb:
                continue
            if not any(('\u4e00' <= c <= '\u9fff') or c in '々〆ヶ' or ('A' <= c <= 'Z') or ('a' <= c <= 'z') for c in b):
                continue
            b, rb = norm_pair(b, rb)
            groups[(b, rb)].append((idx, r))
        print(f'Auditing {len(groups)} unique (base, ruby) pairs...', flush=True)
        flagged = []
        for (b, rb), entries in groups.items():
            rep = entries[0][1]
            ans = ask_audit(args.url, args.model, rep['snippet'], b, rb, rep['raw_ruby'])
            action, new_b, new_rb = 'FLAG', '', ''
            llm_ok = ans.upper().startswith('OK')
            if not llm_ok:
                if ans.upper().startswith('DROP'):
                    action = 'DROP'
                elif ans.upper().startswith('FIX') and '\t' in ans:
                    parts = ans.split('\t')
                    if len(parts) >= 3:
                        action = 'FIX'
                        new_b, new_rb = norm_pair(parts[1].strip(), parts[2].strip())
                        if new_b == b and new_rb == rb:
                            llm_ok = True
                            action, new_b, new_rb = 'FLAG', '', ''
            for idx, r in entries:
                if llm_ok:
                    per = lines_per.get((int(r['page']), int(r['line_id'])), 35.0)
                    reason = suspicious_reason(b, int(r['y0']), int(r['y1']), per)
                    if not reason:
                        continue
                    row_ans = f'RULE:{reason}'
                else:
                    row_ans = ans
                flagged.append({
                    'id': r['id'], 'page': r['page'], 'line_id': r['line_id'],
                    'y0': r['y0'], 'y1': r['y1'], 'raw_ruby': r['raw_ruby'],
                    'snippet': r['snippet'], 'base': b, 'ruby': rb,
                    'action': action, 'new_base': new_b, 'new_ruby': new_rb, 'raw_ans': row_ans,
                })
        args.audit_tsv.parent.mkdir(parents=True, exist_ok=True)
        cols = ['id', 'page', 'line_id', 'y0', 'y1', 'raw_ruby', 'snippet', 'base', 'ruby',
                'action', 'new_base', 'new_ruby', 'raw_ans']
        with args.audit_tsv.open('w', newline='') as f:
            w = csv.DictWriter(f, cols, delimiter='\t', lineterminator='\n')
            w.writeheader()
            w.writerows(flagged)

    human_path = args.human or (args.ruby_tsv.parent / 'ruby-human.tsv')
    human = {}
    human_rows = []
    if human_path.exists():
        for r in csv.DictReader(human_path.open(newline=''), delimiter='\t'):
            r['ruby'] = normalize_ruby(r.get('ruby', ''))
            human[r['id']] = {'v': r['choice'], 'cb': r['base'], 'cr': r['ruby']}
            human_rows.append(r)
    flagged_ids = {it['id'] for it in flagged}
    passthrough = [r for r in human_rows if r['id'] not in flagged_ids]

    by_page = defaultdict(list)
    for i, item in enumerate(flagged):
        by_page[int(item['page'])].append((i, item))

    pdf_path = ROOT / 'input/bookscan-downloads' / args.source
    doc = pypdfium2.PdfDocument(str(pdf_path))
    crops_b64 = {}
    for page, entries in sorted(by_page.items()):
        pimg = doc[page - 1].render(scale=300 / 72).to_pil().convert('L')
        for i, item in entries:
            box = lines_box.get((page, int(item['line_id'])))
            if not box:
                continue
            x0, _, x1, _ = box
            y0, y1 = int(item['y0']), int(item['y1'])
            crop = pimg.crop((int(max(0, x0 - 15)), int(max(0, y0 - 45)),
                              int(min(pimg.width, x1 + 45)), int(min(pimg.height, y1 + 45))))
            crop = crop.resize((crop.width * 2, crop.height * 2))
            buf = io.BytesIO()
            crop.save(buf, format='JPEG', quality=88)
            crops_b64[i] = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
    doc.close()

    sections, data = [], []
    for i, it in enumerate(flagged):
        esc = html.escape
        img_tag = f'<img src="{crops_b64[i]}">' if i in crops_b64 else ''
        has_fix = bool(it['action'] == 'FIX' and it['new_base'] and it['new_ruby'])
        fix_opt = (f'<label><input type="radio" name="r{i}" value="fix">'
                   f'<span class="fix">提案: {esc(it["new_base"])}《{esc(it["new_ruby"])}》</span></label>'
                   if has_fix else '')
        data.append({
            'id': it['id'], 'page': it['page'], 'line_id': it['line_id'],
            'base': it['base'], 'ruby': it['ruby'],
            'new_base': it['new_base'], 'new_ruby': it['new_ruby'],
            'sig': it['base'] + '|' + it['ruby'],
            'human': human.get(it['id']),
        })
        sections.append(f"""
        <section id="s{i}">
          <div class="img">{img_tag}</div>
          <div class="info">
            <h2>#{i+1} p.{esc(it['page'])}:{esc(it['line_id'])}</h2>
            <p>現在: <b>{esc(it['base'])}《{esc(it['ruby'])}》</b>（元OCR: <code>{esc(it['raw_ruby'])}</code>）</p>
            <p class="snip">スニペット: {esc(it['snippet'])}</p>
            <div class="opts">
              <label><input type="radio" name="r{i}" value="keep">今のまま</label>
              {fix_opt}
              <label><input type="radio" name="r{i}" value="drop"><span class="drop">削除</span></label>
              <label><input type="radio" name="r{i}" value="custom">自分で書く:</label>
              <input type="text" id="cb{i}" size="8" value="{esc(it['new_base'] or it['base'])}" placeholder="親文字">
              《<input type="text" id="cr{i}" size="10" value="{esc(it['new_ruby'] or it['ruby'])}" placeholder="ルビ">》
              <button class="upto" data-n="{i}">ここまで「今のまま」</button>
            </div>
          </div>
        </section>""")

    args.review_html.parent.mkdir(parents=True, exist_ok=True)
    args.review_html.write_text(PAGE.format(
        title=html.escape(args.title), count=len(flagged),
        human=html.escape(str(human_path.resolve())),
        save_path=json.dumps(shlex.quote(str(human_path.resolve()))),
        sections='\n'.join(sections),
        items=json.dumps(data, ensure_ascii=False),
        passthrough=json.dumps(passthrough, ensure_ascii=False),
        header='\\t'.join(HUMAN_COLS),
    ), encoding='utf-8')
    print(f'Interactive ruby review ({len(flagged)} items) -> {args.review_html}')


if __name__ == '__main__':
    main()
