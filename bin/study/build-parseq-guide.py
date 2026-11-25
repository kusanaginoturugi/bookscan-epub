#!/usr/bin/env python3
"""Build a visual HTML guide showing what NDLOCR-Lite's PARSeq models actually see
under pad=0, fixed +30px, aspect-ratio padding, and local word-crop re-recognition.

Writes review/parseq-padding-guide/index.html (gitignored; contains real book crops).
"""

import base64
import csv
import difflib
import html
import importlib.util
import io
import sys
from pathlib import Path

import cv2
import numpy as np
import pypdfium2
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sensitivity = load_mod('ocr_sensitivity', ROOT / 'bin/ocr-sensitivity.py')
voicing = load_mod('voicing_review', ROOT / 'bin/build-voicing-review.py')
judge = load_mod('judge', ROOT / 'bin/judge-ocr-disagreements.py')
proof_mod = load_mod('proofread', ROOT / 'bin/build-proofread-sample.py')


def b64_png(arr, scale=1):
    pil = Image.fromarray(arr)
    if scale != 1:
        pil = pil.resize((max(1, pil.width * scale), max(1, pil.height * scale)), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    pil.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


def parseq_view(img, width):
    """Exact geometric transform parseq.py applies before feeding the model:
    rotate 90 CCW if vertical (h > w*0.8), then stretch to (width, 24)."""
    h, w = img.shape[:2]
    if h > w * 0.8:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return cv2.resize(img, (width, 24), interpolation=cv2.INTER_LINEAR)


def diff_html(pred, truth):
    out = []
    p, t = proof_mod.bare(pred), proof_mod.bare(truth)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, p, t, autojunk=False).get_opcodes():
        if op == 'equal':
            out.append(html.escape(p[i1:i2]))
        elif op == 'replace':
            out.append(f'<span class="err" title="正解: {html.escape(t[j1:j2])}">{html.escape(p[i1:i2])}</span>')
        elif op == 'delete':
            out.append(f'<span class="err" title="余分な字">{html.escape(p[i1:i2])}</span>')
        elif op == 'insert':
            out.append(f'<span class="miss" title="脱落">[{html.escape(t[j1:j2])}]</span>')
    return ''.join(out)


def load_page_line(slug, page, line_id):
    book = ROOT / 'work/searchable' / slug
    ocr_dir = book / 'ocr'
    stem = sorted(ocr_dir.glob('*.json'))[0].stem.rsplit('_', 1)[0]
    pdf = ROOT / 'input/bookscan-downloads' / f'{stem}.pdf'
    xml_lines = sensitivity.load_lines(ocr_dir / f'{stem}_{page:05}.xml')
    line = xml_lines[line_id]
    doc = pypdfium2.PdfDocument(str(pdf))
    pimg = np.array(doc[page - 1].render(scale=300 / 72).to_pil().convert('RGB'))
    doc.close()
    tight = sensitivity.crop(pimg, line['x'], line['y'], line['w'], line['h'])
    truth = ''
    pf = book / 'proofread-human.tsv'
    if pf.exists():
        for r in csv.DictReader(pf.open(), delimiter='\t'):
            if int(r['page']) == page and int(r['line_id']) == line_id:
                truth = r['truth']
                break
    return pimg, line, tight, truth


def find_50char_example(slug):
    book = ROOT / 'work/searchable' / slug
    ocr_dir = book / 'ocr'
    stem = sorted(ocr_dir.glob('*.json'))[0].stem.rsplit('_', 1)[0]
    for r in csv.DictReader((book / 'proofread-human.tsv').open(), delimiter='\t'):
        if r['choice'] == 'skip':
            continue
        p, lid = int(r['page']), int(r['line_id'])
        lines = sensitivity.load_lines(ocr_dir / f'{stem}_{p:05}.xml')
        ln = lines[lid]
        if ln['count'] == 2.0 and ln['h'] >= 900:
            return p, lid
    return None


def main():
    models = sensitivity.recognizers('cpu')  # [30(256), 50(384), 100(768)]

    def cascade_read(img, count):
        if count == 3:
            s = models[0].read(img)
            if len(s) < 25:
                return s, 256
            s = models[1].read(img)
            if len(s) < 45:
                return s, 384
            return models[2].read(img), 768
        elif count == 2:
            s = models[1].read(img)
            if len(s) < 45:
                return s, 384
            return models[2].read(img), 768
        return models[2].read(img), 768

    # 1) Long line where pad=0 drops half the line (hoshi p.114:1)
    _, line1, tight1, truth1 = load_page_line('hoshi-wo-tsugu-mono', 114, 1)
    # 2) 50-char model line (count == 2.0) to show why aspect padding breaks 50-char model
    p2, lid2 = find_50char_example('hoshi-wo-tsugu-mono')
    _, line2, tight2, truth2 = load_page_line('hoshi-wo-tsugu-mono', p2, lid2)

    def render_line_demo(title, subtitle, line, tight, truth):
        W_init = 256 if line['count'] == 3 else (384 if line['count'] == 2 else 768)
        conds = [
            ('① 生の切り出し（余白 +0px）', tight, 'そのまま縦横比を無視して押し込む。上下に余裕がなく、文字の端が枠に張り付く。'),
            ('② 現行：固定 +30px 余白', sensitivity.pad_to_width(tight, tight.shape[1] + 30),
             '左右（回転後は上下）に15pxずつ白余白を追加。24pxの中で文字本体が中央13〜14pxに綺麗に収まる。'),
            (f'③ アスペクト比正規化（alpha=1.50, W={W_init}）',
             sensitivity.pad_to_width(tight, max(tight.shape[1], int(round(1.5 * line['h'] * 24.0 / W_init)))),
             f'目標幅 {int(round(1.5 * line["h"] * 24.0 / W_init))}px（元幅 {tight.shape[1]}px）。'
             + ('100文字モデルでは偶然 +30px に近い。' if W_init == 768 else '50文字モデルでは余白が過剰に入り、文字が細く潰れてしまう！')),
        ]
        blocks = []
        for label, img, note in conds:
            pred, used_W = cascade_read(img, line['count'])
            view = parseq_view(img, used_W)
            # Show left 260px crop at 3x zoom so individual characters are crystal clear, plus full 24xW strip
            head_zoom = view[:, :min(260, used_W), :]
            errs = proof_mod.distance(proof_mod.bare(pred), truth)
            blocks.append(f"""
            <div class="card">
              <h3>{html.escape(label)} <span class="meta">入力枠: {img.shape[1]}×{img.shape[0]}px → モデル入力: {used_W}×24px / 誤り {errs}字</span></h3>
              <p class="note">{html.escape(note)}</p>
              <div class="img-label">モデルに実際に入る画像（原寸 {used_W}×24px）:</div>
              <div class="strip"><img src="{b64_png(view, 1)}"></div>
              <div class="img-label">先頭部分の3倍拡大（ピクセルそのまま）:</div>
              <div class="strip"><img src="{b64_png(head_zoom, 3)}"></div>
              <div class="ocr-out"><b>認識結果:</b> {diff_html(pred, truth)}</div>
            </div>""")
        return f"""
        <section>
          <h2>{html.escape(title)}</h2>
          <p class="sub">{html.escape(subtitle)}（元枠: 幅{line['w']}px × 高さ{line['h']}px、pred_char_cnt={int(line['count'])} → 初期モデル W={W_init}）</p>
          <p class="truth"><b>正解テキスト:</b> {html.escape(truth)}</p>
          {''.join(blocks)}
        </section>"""

    # 3) Word-crop demo: proteus 52:9 (ギ->キ) and hoshi 205:12 (』->】)
    crop_examples = [
        ('proteus-operation-jo', 52, 9, 'ギ', 'キ', 'ニューメ[ギ→キ]シ（濁点の誤認）'),
        ('hoshi-wo-tsugu-mono', 205, 12, '』', '】', '許し難いのだ。[』→】]（括弧の取り違え）'),
    ]
    crop_cards = []
    for slug, page, lid, frm, to, label in crop_examples:
        pimg, line, tight, truth = load_page_line(slug, page, lid)
        # Get margin30 text as judge sees it
        padded30 = sensitivity.pad_to_width(tight, tight.shape[1] + 30)
        text30, used_W = cascade_read(padded30, line['count'])
        pos = text30.find(frm)
        if pos < 0:
            # fallback search near end
            pos = len(text30) - 1
        line_dict = {
            'box': (line['x'], line['y'], line['x'] + line['w'], line['y'] + line['h']),
            'text': text30,
            'vertical': line['h'] > line['w'],
        }
        w0, w1 = max(0, pos - 2), min(len(text30), pos + 3)
        wcrop = voicing.word_crop(pimg, line_dict, w0, w1, margin=0.3)
        # Compare what model sees in full line vs word crop (in 30-char model 24x256)
        full_view = parseq_view(padded30, used_W)
        # Approximate x-range in rotated full_view
        fx0 = int(max(0, (w0 / max(len(text30), 1)) * used_W - 10))
        fx1 = int(min(used_W, (w1 / max(len(text30), 1)) * used_W + 10))
        full_slice = full_view[:, fx0:fx1, :]
        crop_view = parseq_view(wcrop, 256)
        # Compute 3-model margin
        m_total, m_used = judge.char_margin(models, wcrop, text30[w0:pos], frm, to, text30[pos + 1:w1])
        # Per-model margins
        per_m = []
        for name, mod in zip(('30字(24×256)', '50字(24×384)', '100字(24×768)'), models):
            mv, mu = judge.char_margin([mod], wcrop, text30[w0:pos], frm, to, text30[pos + 1:w1])
            per_m.append(f'{name}: {mv:+.2f}' if mv is not None else f'{name}: N/A')
        crop_cards.append(f"""
        <div class="card">
          <h3>{html.escape(label)} <span class="meta">({slug} p.{page}:{lid})</span></h3>
          <div class="compare-grid">
            <div>
              <div class="img-label">① 行全体（{used_W}×24px）の中の該当部分（4倍拡大）</div>
              <img src="{b64_png(full_slice, 4)}">
              <p class="note">1文字あたり約18×13pxに潰れている → ここで <code>{html.escape(frm)}</code> と誤認識</p>
            </div>
            <div>
              <div class="img-label">② 局所切り出し（前後2文字だけ）を 256×24px に入れた画像（2倍拡大）</div>
              <img src="{b64_png(crop_view, 2)}">
              <p class="note">1文字あたりの横幅が約3倍に広がり、字形がはっきり見える！</p>
            </div>
          </div>
          <div class="margin-box">
            <b>PARSeq 3モデルの対数確率差 log P(NDL:{html.escape(frm)}) − log P(YomiToku:{html.escape(to)}):</b>
            <span class="neg">{m_total:+.2f}</span>（マイナス＝画像が強く <b>{html.escape(to)}</b> を支持）<br>
            <span class="meta">内訳: {' ／ '.join(per_m)}</span>
          </div>
        </div>""")

    out_html = f"""<!doctype html>
<html lang="ja"><meta charset="utf-8">
<title>NDLOCR-Lite（PARSeq）画像入力と余白・局所切り出しの解説</title>
<style>
body{{font:15px/1.6 sans-serif;max-width:1100px;margin:1.5em auto;padding:0 1.2em;color:#222;background:#fafafa}}
h1{{border-bottom:3px solid #333;padding-bottom:.3em}}
h2{{margin-top:2em;border-left:5px solid #2563eb;padding-left:.6em}}
h3{{margin:.2em 0 .4em;font-size:1.05em}}
.sub{{color:#555;margin-top:-.4em}}
.meta{{font-weight:normal;font-size:.88em;color:#666;margin-left:.5em}}
.card{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1em 1.2em;margin:1em 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.note{{color:#444;font-size:.93em;margin:.3em 0 .6em}}
.img-label{{font-size:.85em;color:#666;margin:.5em 0 .2em}}
.strip{{overflow-x:auto;background:#eee;padding:6px;border-radius:4px}}
.strip img{{display:block;image-rendering:pixelated}}
.ocr-out{{margin-top:.7em;padding:.5em .7em;background:#f8fafc;border-left:3px solid #94a3b8;font-family:"Noto Serif CJK JP",serif;font-size:1.05em}}
.truth{{padding:.5em .8em;background:#ecfdf5;border-left:4px solid #10b981;font-family:"Noto Serif CJK JP",serif}}
.err{{background:#fee2e2;color:#b91c1c;font-weight:bold;padding:0 2px;border-radius:2px}}
.miss{{background:#fef3c7;color:#b45309;font-weight:bold;padding:0 2px;border-radius:2px}}
.compare-grid{{display:flex;flex-wrap:wrap;gap:1.5em;align-items:flex-start}}
.compare-grid img{{display:block;border:1px solid #ccc;image-rendering:pixelated}}
.margin-box{{margin-top:.8em;padding:.6em .8em;background:#eff6ff;border-left:4px solid #2563eb}}
.neg{{font-size:1.2em;font-weight:bold;color:#dc2626}}
table{{border-collapse:collapse;background:#fff;width:100%;margin:1em 0}}
th,td{{border:1px solid #ddd;padding:.45em .7em;text-align:left}}
th{{background:#f1f5f9}}
tr.best td{{background:#ecfdf5;font-weight:bold}}
</style>
<h1>NDLOCR-Lite（PARSeq）は何を見ているか — 実例画像ガイド</h1>
<p>NDLOCR-Lite の文字認識（PARSeq）は、縦書きの1行を反時計回りに90度回転させたあと、<b>縦横比を無視して固定サイズ <code>24 × W</code>（30字モデル=256、50字モデル=384、100字モデル=768）に強制リサイズ</b>して読む。このページでは、モデルに実際に入力される画像を原寸と拡大で並べて仕組みを可視化する。</p>

{render_line_demo('1. 長い行（100文字モデル W=768）でなぜ +30px 余白が劇的に効くか',
                  '『星を継ぐもの』p.114 行1 — 生の読みでは後半28字が丸ごと脱落していた行',
                  line1, tight1, truth1)}

{render_line_demo('2. 50文字モデル（W=384）でなぜ「行長に比例したアスペクト比余白」が逆効果だったか',
                  f'『星を継ぐもの』p.{p2} 行{lid2} — 50文字モデルに振り分けられた行',
                  line2, tight2, truth2)}

<section>
  <h2>3. なぜ「局所切り出し（前後2文字）」の PARSeq 3モデル確率差が 74/74（100%）当たるのか</h2>
  <p class="sub">行全体（40字）を 768×24px に詰め込むと1文字は幅18pxほどに潰れるが、前後2文字（計5字）だけ切り出して 256×24px に入れると1文字あたりの解像度が約3倍に跳ね上がる。</p>
  {''.join(crop_cards)}
</section>

<section>
  <h2>4. 正解データ400行（14,662字）での余白ベンチマークまとめ</h2>
  <p class="sub">『星を継ぐもの』200行＋『プロテウス・オペレーション（上）』200行の抜き取り検査正解で実測。</p>
  <table>
    <tr><th>条件</th><th>誤り字数</th><th>誤り行数</th><th>CER</th><th>ポイント</th></tr>
    <tr><td>生の読み（余白 +0px）</td><td>161</td><td>47 / 400</td><td>1.098%</td><td>長い行の後半脱落や括弧落ちが多い</td></tr>
    <tr><td>固定 +25px（h≥400）</td><td>66</td><td>40 / 400</td><td>0.450%</td><td>+30px とほぼ同等</td></tr>
    <tr class="best"><td>現行：固定 +30px（h≥400）</td><td>65</td><td>39 / 400</td><td>0.443%</td><td>24px高さの中で文字本体13〜14px＋上下白余白5pxが最適点</td></tr>
    <tr><td>固定 +35px（h≥400）</td><td>79</td><td>50 / 400</td><td>0.539%</td><td>文字が小さくなり始めて悪化</td></tr>
    <tr><td>固定 +40px（h≥400）</td><td>86</td><td>57 / 400</td><td>0.587%</td><td>さらに悪化</td></tr>
    <tr><td>カスケード＋アスペクト比正規化（alpha=1.0〜2.5）</td><td>172〜925</td><td>56〜189 / 400</td><td>1.173%〜</td><td>50/30字モデルに過剰な余白が入り文字が潰れる</td></tr>
    <tr><td>強制50字モデル＋アスペクト比正規化（alpha=0.75〜1.5）</td><td>100〜1696</td><td>73〜306 / 400</td><td>0.682%〜</td><td>50字モデル単独より既定カスケード+30pxが強い</td></tr>
  </table>
</section>
</html>"""

    out_path = ROOT / 'review/parseq-padding-guide/index.html'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_html, encoding='utf-8')
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
