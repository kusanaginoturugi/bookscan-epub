#!/usr/bin/env python3
"""Scan OCR'd book pages with a local LLM, verify flagged spots against the
original PDF crops with a local VLM + margin.tsv variants, and generate an
interactive HTML review page that outputs overrides.tsv append commands.
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
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
import pypdfium2

DEFAULT_URL = 'http://127.0.0.1:8080/v1/chat/completions'
DEFAULT_MODEL = 'dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL'

AUDIT_COLS = [
    'page', 'line_id', 'status', 'snippet', 'suspect', 'window',
    'vlm_actual', 'fix_char', 'proposed_line', 'margin_match',
    'margin_note', 'ocr_line',
]

DETECT_PROMPT = """以下は日本語のSF小説をOCRで読み取った1ページ分の本文です。
OCRの誤認識（文字化け、不自然な1文字の取り違え、文脈に合わない文字・記号など）と強く疑われる箇所だけをJSON配列で返してください。
ルール：
- 固有名詞（人名・地名・組織名・星名・SF用語）や文語・古風な表現は絶対に挙げない。
- ページ先頭や末尾の文の途切れは無視する。
- 明らかな異常がなければ [] のみを返す。
- 出力はJSON配列のみ（前置きやコードブロックは不要）。
形式: [{{"snippet": "前後含む5〜12文字", "suspect": "怪しい1〜3文字"}}]

本文：
{text}"""

VLM_PROMPT = """これは日本語の縦書き小説のスキャン画像（上から下に読む）の一部です。
現在のOCR結果は「{window}」で、特に「{suspect}」の部分が誤認識と疑われています。
画像に実際に印刷されている文字をよく見て、次のJSONのみで答えてください（コードブロック不要）。
{{"actual": "画像に実際に書かれている文字列（windowに対応する部分）", "fix_char": "suspectに対応する実際の文字（OCRどおりならsuspectと同じ文字）"}}"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: sans-serif; max-width: 1100px; margin: 1.5rem auto; padding: 0 1rem; color: #222; }}
  header {{ border-bottom: 1px solid #ccc; padding-bottom: .8rem; margin-bottom: 1rem; }}
  .summary {{ color: #555; font-size: .95rem; }}
  .bar {{ position: sticky; top: 0; background: #fff; border-bottom: 1px solid #ddd; padding: .6rem 0; z-index: 10; display: flex; gap: .8rem; align-items: center; flex-wrap: wrap; }}
  section {{ display: flex; gap: 1.2rem; border: 1px solid #ddd; border-radius: 6px; padding: .9rem; margin-bottom: 1rem; background: #fafafa; }}
  section.high {{ border-left: 5px solid #c62828; background: #fff8f8; }}
  .img {{ flex: 0 0 auto; }}
  .img img {{ max-height: 360px; border: 1px solid #bbb; background: #fff; }}
  .info {{ flex: 1 1 auto; min-width: 0; }}
  h2 {{ margin: 0 0 .4rem 0; font-size: 1.05rem; }}
  .badge {{ display: inline-block; font-size: .78rem; padding: .1rem .45rem; border-radius: 4px; margin-left: .4rem; vertical-align: middle; }}
  .badge.high {{ background: #c62828; color: #fff; }}
  .badge.vlm {{ background: #ef6c00; color: #fff; }}
  .line {{ font-family: monospace; background: #fff; border: 1px solid #e0e0e0; padding: .35rem .5rem; border-radius: 4px; margin: .3rem 0; word-break: break-all; }}
  mark.bad {{ background: #ffcdd2; padding: 0 2px; }}
  mark.good {{ background: #c8e6c9; padding: 0 2px; }}
  .meta {{ font-size: .88rem; color: #555; margin: .3rem 0; }}
  .opts {{ margin-top: .6rem; display: flex; gap: .8rem; flex-wrap: wrap; align-items: center; }}
  .custom-box {{ width: 100%; margin-top: .4rem; font-family: monospace; font-size: .95rem; padding: .3rem .4rem; box-sizing: border-box; }}
  button {{ cursor: pointer; padding: .35rem .7rem; }}
  #cmd {{ width: 100%; font-family: monospace; font-size: .85rem; margin-top: .4rem; display: none; }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <p class="summary">候補 {count} 件（高信頼 margin一致: {high_count} 件 / VLM差分のみ: {vlm_count} 件）。反映先: <code>{overrides_path}</code></p>
</header>
<div class="bar">
  <button id="copyBtn">overrides.tsv 追記コマンドをコピー</button>
  <button id="selectHighBtn">高信頼のみ「提案採用」にする</button>
  <span id="status"></span>
  <textarea id="cmd" rows="5" readonly></textarea>
</div>
{sections}
<script>
const ITEMS = {items_json};
const OVERRIDES = {overrides_sh};
const KEY = 'llm-audit:' + OVERRIDES;

function loadState() {{
  try {{ return JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{ return {{}}; }}
}}
function saveState() {{
  const st = {{}};
  ITEMS.forEach((it, i) => {{
    const checked = document.querySelector(`input[name="r${{i}}"]:checked`);
    const custom = document.getElementById(`c${{i}}`).value;
    st[it.key] = {{ choice: checked ? checked.value : 'keep', custom }};
  }});
  try {{ localStorage.setItem(KEY, JSON.stringify(st)); }} catch (e) {{}}
  updateStatus();
}}
function updateStatus() {{
  let fixes = 0;
  ITEMS.forEach((it, i) => {{
    const checked = document.querySelector(`input[name="r${{i}}"]:checked`);
    if (checked && checked.value !== 'keep') fixes++;
  }});
  document.getElementById('status').textContent = `修正選択: ${{fixes}} / ${{ITEMS.length}} 件`;
}}
function buildCommand() {{
  const lines = [];
  ITEMS.forEach((it, i) => {{
    const checked = document.querySelector(`input[name="r${{i}}"]:checked`);
    if (!checked || checked.value === 'keep') return;
    const corrected = checked.value === 'fix' ? it.proposed_line : document.getElementById(`c${{i}}`).value.trim();
    if (!corrected || corrected === it.ocr_line) return;
    lines.push([it.page, it.line_id, it.ocr_line, corrected].join('\\t'));
  }});
  if (!lines.length) return '# 修正対象が選択されていません';
  return `cat <<'EOF' >> ${{OVERRIDES}}\\n${{lines.join('\\n')}}\\nEOF`;
}}

const saved = loadState();
ITEMS.forEach((it, i) => {{
  const s = saved[it.key];
  const defChoice = s ? s.choice : (it.margin_match ? 'fix' : 'keep');
  const radio = document.querySelector(`input[name="r${{i}}"][value="${{defChoice}}"]`);
  if (radio) radio.checked = true;
  if (s && s.custom) document.getElementById(`c${{i}}`).value = s.custom;
  document.querySelectorAll(`input[name="r${{i}}"]`).forEach(el => el.addEventListener('change', saveState));
  document.getElementById(`c${{i}}`).addEventListener('input', () => {{
    document.querySelector(`input[name="r${{i}}"][value="custom"]`).checked = true;
    saveState();
  }});
}});
document.querySelectorAll('.upto').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const n = Number(btn.dataset.n);
    for (let i = 0; i <= n; i++) {{
      const cur = document.querySelector(`input[name="r${{i}}"]:checked`);
      if (!cur || cur.value === 'keep') {{
        document.querySelector(`input[name="r${{i}}"][value="keep"]`).checked = true;
      }}
    }}
    saveState();
  }});
}});
document.getElementById('selectHighBtn').addEventListener('click', () => {{
  ITEMS.forEach((it, i) => {{
    const v = it.margin_match ? 'fix' : 'keep';
    document.querySelector(`input[name="r${{i}}"][value="${{v}}"]`).checked = true;
  }});
  saveState();
}});
document.getElementById('copyBtn').addEventListener('click', async () => {{
  const cmd = buildCommand();
  const box = document.getElementById('cmd');
  box.value = cmd;
  box.style.display = 'block';
  try {{
    await navigator.clipboard.writeText(cmd);
    document.getElementById('status').textContent += '（コピー済み）';
  }} catch (e) {{}}
}});
updateStatus();
</script>
</body>
</html>
"""


def parse_pages(spec):
    if not spec:
        return None
    pages = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return pages


def post_chat(url, model, messages, max_tokens=256, repeat_penalty=1.15):
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.0,
        'max_tokens': max_tokens,
        'repeat_penalty': repeat_penalty,
        'chat_template_kwargs': {'enable_thinking': False},
    }
    req = urllib.request.Request(
        url,
        json.dumps(body, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return (data['choices'][0]['message'].get('content') or '').strip()


def parse_json_array(raw):
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.S).strip()
    m = re.search(r'\[.*\]', s, flags=re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


def parse_json_obj(raw):
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.S).strip()
    m = re.search(r'\{.*\}', s, flags=re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def detect_catalog_pages(by_page, tail=20, min_lines=30, max_average=16):
    found = set()
    for p in sorted(by_page)[-tail:]:
        texts = [ln['final'] for ln in by_page[p]]
        if len(texts) >= min_lines and (sum(map(len, texts)) / len(texts)) < max_average:
            found.add(p)
    return found


def load_lines(path):
    by_page = {}
    by_key = {}
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            p = int(row['page'])
            lid = int(row['line_id'])
            by_page.setdefault(p, []).append(row)
            by_key[(p, lid)] = row
    return by_page, by_key


def load_margins(path):
    margins = {}
    if not path or not path.exists():
        return margins
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            margins[(int(row['page']), int(row['line_id']))] = row
    return margins


def locate_in_lines(page_lines, snippet, suspect):
    for ln in page_lines:
        txt = ln['final']
        idx = txt.find(snippet)
        if idx >= 0:
            s_idx = txt.find(suspect, idx, idx + len(snippet)) if suspect else idx
            return ln, (s_idx if s_idx >= 0 else idx)
    joined = ''
    spans = []
    for ln in page_lines:
        start = len(joined)
        joined += ln['final']
        spans.append((start, len(joined), ln))
    pos = joined.find(snippet)
    if pos < 0 and suspect:
        pos = joined.find(suspect)
    if pos >= 0:
        s_pos = joined.find(suspect, pos, pos + max(len(snippet), len(suspect))) if suspect else pos
        if s_pos < 0:
            s_pos = pos
        for start, end, ln in spans:
            if start <= s_pos < end:
                return ln, s_pos - start
    return None, -1


def crop_vertical_window(pimg, box_str, char_idx, line_len, suspect_len):
    x0, y0, x1, y1 = (int(round(float(v))) for v in box_str.split(','))
    line_len = max(line_len, 1)
    pad_chars = 5
    start_c = max(0, char_idx - pad_chars)
    end_c = min(line_len, char_idx + max(suspect_len, 1) + pad_chars)
    h = max(y1 - y0, 1)
    cy0 = max(0, y0 + int(h * (start_c / line_len)) - 12)
    cy1 = min(pimg.height, y0 + int(h * (end_c / line_len)) + 12)
    cx0 = max(0, x0 - 16)
    cx1 = min(pimg.width, x1 + 16)
    crop = pimg.crop((cx0, cy0, cx1, cy1))
    return crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.BICUBIC)


def check_margin_match(mrow, proposed_line, suspect, fix_char):
    if not mrow:
        return False, ''
    cols = ['saved', 'margin0', 'margin8', 'margin14', 'margin20']
    norm_prop = proposed_line.replace(' ', '')
    m30 = mrow.get('margin30', '').replace(' ', '')
    if norm_prop == m30:
        return False, ''
    variants = {}
    matched_cols = []
    for c in cols:
        v = mrow.get(c, '').replace(' ', '')
        if not v:
            continue
        variants.setdefault(v, []).append(c)
        if v == norm_prop:
            matched_cols.append(c)
    if matched_cols:
        return True, f"一致({','.join(matched_cols)})"
    if suspect and fix_char and suspect in m30:
        for v, cs in variants.items():
            if v != m30 and fix_char in v and suspect not in v:
                return True, f"部分一致({','.join(cs)})"
    if len(variants) > 1 or (variants and any(v != m30 for v in variants)):
        return False, f"margin差分あり({len(variants)}種)"
    return False, ''


def normalize_ws(s):
    return re.sub(r'\s+', '', s or '')


def build_proposed_line(line_text, cidx, suspect, fix_char):
    if not fix_char or fix_char == suspect:
        return line_text
    if line_text[cidx:cidx + len(suspect)] == suspect:
        return line_text[:cidx] + fix_char + line_text[cidx + len(suspect):]
    return line_text.replace(suspect, fix_char, 1)


def is_real_diff(window, suspect, actual, fix_char):
    if not actual and not fix_char:
        return False
    nw = normalize_ws(window)
    na = normalize_ws(actual)
    if na and (na == nw or na in nw or nw in na):
        if not fix_char or normalize_ws(fix_char) == normalize_ws(suspect):
            return False
    if fix_char and normalize_ws(fix_char) != normalize_ws(suspect):
        return True
    return bool(na and na != nw and na not in nw and nw not in na)


def load_audit_cache(path):
    rows = []
    scanned_pages = set()
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                rows.append(row)
                scanned_pages.add(int(row['page']))
    return rows, scanned_pages


def render_html(args, audit_rows, by_key, margins):
    candidates = [
        r for r in audit_rows
        if r['status'] == 'diff' and r.get('proposed_line') and r['proposed_line'] != r['ocr_line']
    ]
    for r in candidates:
        mmatch, mnote = check_margin_match(
            margins.get((int(r['page']), int(r['line_id']))),
            r['proposed_line'], r['suspect'], r['fix_char'],
        )
        r['margin_match'] = '1' if mmatch else '0'
        if mnote:
            r['margin_note'] = mnote
    # High confidence (margin_match) first, then page/line order
    candidates.sort(key=lambda r: (0 if r['margin_match'] == '1' else 1, int(r['page']), int(r['line_id'])))

    doc = pypdfium2.PdfDocument(str(args.pdf))
    page_imgs = {}
    sections = []
    js_items = []
    high_count = 0
    try:
        for i, r in enumerate(candidates):
            p = int(r['page'])
            lid = int(r['line_id'])
            ln = by_key.get((p, lid))
            img_tag = ''
            if ln:
                if p not in page_imgs:
                    page_imgs[p] = doc[p - 1].render(scale=300 / 72).to_pil().convert('RGB')
                cidx = ln['final'].find(r['suspect'])
                if cidx < 0:
                    cidx = 0
                crop = crop_vertical_window(page_imgs[p], ln['box'], cidx, len(ln['final']), len(r['suspect']))
                buf = io.BytesIO()
                crop.save(buf, format='JPEG', quality=90)
                b64 = base64.b64encode(buf.getvalue()).decode('ascii')
                img_tag = f'<img src="data:image/jpeg;base64,{b64}">'

            is_high = r['margin_match'] == '1'
            if is_high:
                high_count += 1
            esc = html.escape
            ocr_line = r['ocr_line']
            prop_line = r['proposed_line'] or ocr_line
            suspect = r['suspect']
            fix_char = r['fix_char']

            ocr_html = esc(ocr_line).replace(esc(suspect), f'<mark class="bad">{esc(suspect)}</mark>', 1) if suspect else esc(ocr_line)
            prop_html = esc(prop_line).replace(esc(fix_char), f'<mark class="good">{esc(fix_char)}</mark>', 1) if fix_char else esc(prop_line)
            badge = '<span class="badge high">高信頼 (margin一致)</span>' if is_high else '<span class="badge vlm">VLM差分</span>'
            m_note = f' / margin: {esc(r["margin_note"])}' if r['margin_note'] else ''

            sections.append(f"""
<section class="{'high' if is_high else ''}" id="s{i}">
  <div class="img">{img_tag}</div>
  <div class="info">
    <h2>#{i + 1} p.{p}:{lid} {badge}</h2>
    <div class="meta">怪しい箇所: <b>{esc(suspect)}</b> → VLM読み: <b>{esc(fix_char or r['vlm_actual'])}</b>{m_note}</div>
    <div class="line">現在: {ocr_html}</div>
    <div class="line">提案: {prop_html}</div>
    <div class="opts">
      <label><input type="radio" name="r{i}" value="keep">今のまま</label>
      <label><input type="radio" name="r{i}" value="fix">提案を採用</label>
      <label><input type="radio" name="r{i}" value="custom">自分で書く</label>
      <button type="button" class="upto" data-n="{i}">ここまで「今のまま」</button>
    </div>
    <input type="text" class="custom-box" id="c{i}" value="{esc(prop_line)}">
  </div>
</section>""")
            js_items.append({
                'key': f'{p}:{lid}:{suspect}',
                'page': p,
                'line_id': lid,
                'ocr_line': ocr_line,
                'proposed_line': prop_line,
                'margin_match': is_high,
            })
    finally:
        doc.close()

    args.review_html.parent.mkdir(parents=True, exist_ok=True)
    ovr = args.overrides.resolve()
    args.review_html.write_text(PAGE_HTML.format(
        title=html.escape(args.title),
        count=len(candidates),
        high_count=high_count,
        vlm_count=len(candidates) - high_count,
        overrides_path=html.escape(str(ovr)),
        overrides_sh=json.dumps(shlex.quote(str(ovr))),
        sections='\n'.join(sections),
        items_json=json.dumps(js_items, ensure_ascii=False),
    ), encoding='utf-8')
    print(f'Review HTML ({len(candidates)} items: {high_count} high, {len(candidates) - high_count} vlm-only) -> {args.review_html}')


def main():
    parser = argparse.ArgumentParser(description='Audit OCR lines with local LLM + VLM crop check.')
    parser.add_argument('pdf', type=Path, help='PDF file matching lines.tsv page numbers and boxes')
    parser.add_argument('lines_tsv', type=Path, help='work/searchable/<book>/lines.tsv')
    parser.add_argument('review_html', type=Path, help='Output HTML review file')
    parser.add_argument('--margin-tsv', type=Path, default=None, help='margin.tsv for cross-checking variants')
    parser.add_argument('--audit-tsv', type=Path, default=None, help='Cache/log TSV (default: <lines_tsv dir>/llm-audit.tsv)')
    parser.add_argument('--overrides', type=Path, default=None, help='Target overrides.tsv for save command')
    parser.add_argument('--pages', default=None, help='Page range/list, e.g. 205-224 or 216')
    parser.add_argument('--skip-pages', default=None, help='Pages to skip, e.g. 312-322')
    parser.add_argument('--include-catalog', action='store_true', help='Do not auto-skip back-matter catalog pages')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--title', default='LLM+VLM 本文チェック')
    parser.add_argument('--reuse', action='store_true', help='Skip LLM/VLM calls and rebuild HTML from audit TSV')
    args = parser.parse_args()

    if args.audit_tsv is None:
        args.audit_tsv = args.lines_tsv.parent / 'llm-audit.tsv'
    if args.margin_tsv is None and (args.lines_tsv.parent / 'margin.tsv').exists():
        args.margin_tsv = args.lines_tsv.parent / 'margin.tsv'
    if args.overrides is None:
        args.overrides = args.lines_tsv.parent / 'overrides.tsv'

    by_page, by_key = load_lines(args.lines_tsv)
    margins = load_margins(args.margin_tsv)
    skip_pages = parse_pages(args.skip_pages) or set()
    skip_file = args.lines_tsv.parent / 'skip-pages.txt'
    if skip_file.exists():
        skip_pages |= parse_pages(skip_file.read_text(encoding='utf-8').strip()) or set()
    if not args.include_catalog:
        skip_pages |= detect_catalog_pages(by_page)
    target_pages = parse_pages(args.pages)
    all_pages = sorted(p for p in by_page if (target_pages is None or p in target_pages) and p not in skip_pages)

    audit_rows, scanned_pages = load_audit_cache(args.audit_tsv)

    if not args.reuse:
        todo = [p for p in all_pages if p not in scanned_pages]
        if todo:
            args.audit_tsv.parent.mkdir(parents=True, exist_ok=True)
            write_header = not args.audit_tsv.exists()
            doc = pypdfium2.PdfDocument(str(args.pdf))
            t_start = time.time()
            try:
                with args.audit_tsv.open('a', newline='', encoding='utf-8') as handle:
                    writer = csv.DictWriter(handle, AUDIT_COLS, delimiter='\t', lineterminator='\n')
                    if write_header:
                        writer.writeheader()
                    for p in todo:
                        plines = by_page[p]
                        page_text = ''.join(ln['final'] for ln in plines)
                        t0 = time.time()
                        try:
                            raw = post_chat(args.url, args.model, [
                                {'role': 'user', 'content': DETECT_PROMPT.format(text=page_text)},
                            ], max_tokens=256)
                        except (urllib.error.URLError, TimeoutError, OSError) as exc:
                            print(f'p.{p}: LLM error: {exc}', file=sys.stderr)
                            continue
                        items = [it for it in parse_json_array(raw) if isinstance(it, dict) and it.get('suspect')]
                        dt1 = time.time() - t0
                        if not items:
                            row = {c: '' for c in AUDIT_COLS}
                            row.update({'page': str(p), 'line_id': '-1', 'status': 'clean'})
                            writer.writerow(row)
                            audit_rows.append(row)
                            handle.flush()
                            print(f'p.{p:3d} ({dt1:4.2f}s): clean', flush=True)
                            continue

                        pimg = doc[p - 1].render(scale=300 / 72).to_pil().convert('RGB')
                        diffs = 0
                        for it in items:
                            snippet = str(it.get('snippet', ''))
                            suspect = str(it.get('suspect', ''))
                            ln, cidx = locate_in_lines(plines, snippet, suspect)
                            if ln is None:
                                row = {c: '' for c in AUDIT_COLS}
                                row.update({'page': str(p), 'line_id': '-1', 'status': 'unlocated',
                                            'snippet': snippet, 'suspect': suspect})
                                writer.writerow(row)
                                audit_rows.append(row)
                                continue
                            lid = int(ln['line_id'])
                            txt = ln['final']
                            window = txt[max(0, cidx - 5):min(len(txt), cidx + len(suspect) + 5)]
                            crop = crop_vertical_window(pimg, ln['box'], cidx, len(txt), len(suspect))
                            buf = io.BytesIO()
                            crop.save(buf, format='PNG')
                            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
                            try:
                                vlm_raw = post_chat(args.url, args.model, [{
                                    'role': 'user',
                                    'content': [
                                        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}},
                                        {'type': 'text', 'text': VLM_PROMPT.format(window=window, suspect=suspect)},
                                    ],
                                }], max_tokens=128)
                            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                                print(f'p.{p}:{lid}: VLM error: {exc}', file=sys.stderr)
                                continue
                            res = parse_json_obj(vlm_raw)
                            actual = str(res.get('actual', ''))
                            fix_char = str(res.get('fix_char', ''))
                            real_diff = is_real_diff(window, suspect, actual, fix_char)
                            proposed = build_proposed_line(txt, cidx, suspect, fix_char) if real_diff else txt
                            mmatch, mnote = check_margin_match(margins.get((p, lid)), proposed, suspect, fix_char)
                            status = 'diff' if real_diff else 'vlm_ok'
                            if real_diff:
                                diffs += 1
                            row = {
                                'page': str(p), 'line_id': str(lid), 'status': status,
                                'snippet': snippet, 'suspect': suspect, 'window': window,
                                'vlm_actual': actual, 'fix_char': fix_char,
                                'proposed_line': proposed,
                                'margin_match': '1' if mmatch else '0',
                                'margin_note': mnote,
                                'ocr_line': txt,
                            }
                            writer.writerow(row)
                            audit_rows.append(row)
                        handle.flush()
                        print(f'p.{p:3d} ({dt1:4.2f}s): {len(items)} flagged -> {diffs} diff', flush=True)
            finally:
                doc.close()
            print(f'Scanned {len(todo)} pages in {time.time() - t_start:.1f}s -> {args.audit_tsv}', flush=True)

    # Filter audit_rows to requested/non-skipped pages
    audit_rows = [
        r for r in audit_rows
        if int(r['page']) not in skip_pages and (target_pages is None or int(r['page']) in target_pages)
    ]
    render_html(args, audit_rows, by_key, margins)


if __name__ == '__main__':
    main()
