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
A simplified kanji (掴) against YomiToku's traditional form of it (摑) takes
YomiToku's by rule; a traditional form from NDL against YomiToku's simplified
one stays (NDL leans to simplified forms, so it saw the old one printed). Lines with many hunks count as misaligned and are skipped. The rest are
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
import shlex
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2

from text_layer_utils import (apply_corrections, catalog_pages, clean_text, fix_brackets, fix_dashes,
                              fix_variants, line_joins, load_corrections, load_glossary, parse_pages)

ROOT = Path(__file__).resolve().parent
PROMPTS = {
    # v1: the one line only.
    'v1': """次のA・Bは、小説をOCRで読み取った同じ1行です。数文字だけ読みが違います。
日本語の文章として自然で正しい方を、AかBの1文字だけで答えてください。

A: {a}

B: {b}""",
    # v2: previous and next lines of the page, the differing part in 【】, and a
    # warning against preferring today's common words (the Summer Door review
    # found most v1 errors on rare or old-fashioned words).
    'v2': """次のA・Bは、小説をOCRで読み取った同じ箇所です。【】の中だけが違います。
小説には、珍しい漢字、古い言い回し、成句、造語、人名が出てきます。今よく使う語だからという理由では選ばず、
前後の文脈に合う方を選んでください。原文として正しい方を、AかBの1文字だけで答えてください。

A: {a}

B: {b}""",
}
VOICING = str.maketrans('バビブベボパピプペポ', 'パピプペポバビブベボ')
SMALL = str.maketrans('ァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎ', 'アイウエオツヤユヨワカケあいうえおつやゆよわ')
# NDLOCR-Lite answers in the simplified form (掴) even where the page prints
# the traditional one (摑), and lacks some of these in its character list
# (頰, 噓, 鷗, 啞, 𠮟, 瘦); YomiToku reads them as printed.
OLD_FORMS = dict(zip('掴噛頬嘘剥填鴎掻屏祷蝋唖叱痩撹麹鹸繍蒋醤蝉槙溌涜騨顛挟挿繋侠頚麺屡蛍渇祢迩箪',
                     '摑嚙頰噓剝塡鷗搔屛禱蠟啞𠮟瘦攪麴鹼繡蔣醬蟬槇潑瀆驒顚挾插繫俠頸麵屢螢渴禰邇簞'))
LINE_SHAPED = set('一「」『』[]\\|―ー-−—')
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


# Katakana and a hiragana or kanji of (almost) the same printed shape: an image
# cannot tell them apart, the neighbouring characters can.
LOOKALIKE = {'ヘ': 'へ', 'ベ': 'べ', 'ペ': 'ぺ', 'ロ': '口', 'ニ': '二', 'エ': '工', 'カ': '力', 'ハ': '八',
             'ト': '卜', 'タ': '夕'}
SAME_SHAPE = {'ヘ', 'ベ', 'ペ'}  # identical glyphs; the others differ slightly


def is_katakana(ch):
    return bool(ch) and ('ァ' <= ch <= 'ヺ' or ch == 'ー')


def script(ch):
    if is_katakana(ch):
        return 'カタカナ'
    if 'ぁ' <= ch <= 'ゖ':
        return 'ひらがな'
    if '一' <= ch <= '鿿':
        return '漢字'
    return ''


def standalone_counts(lines):
    """Katakana words not touching a look-alike character nor split over a line
    break, so リッキイ counts but the ラス of a misread へラス or of ガ|ラス does not."""
    shapes = set(LOOKALIKE) | set(LOOKALIKE.values())
    found = Counter()
    for line in lines:
        text = line['text']
        lo, hi = len(text) - len(text.lstrip()), len(text.rstrip())
        for match in re.finditer(r'[ァ-ヺー]{2,}', text):
            before = text[match.start() - 1] if match.start() else ''
            after = text[match.end()] if match.end() < len(text) else ''
            if before in shapes or after in shapes:
                continue
            if (line['joined'][0] and match.start() == lo) or (line['joined'][1] and match.end() == hi):
                continue
            found[match.group()] += 1
    return found


def lookalike_choice(text, start, end, kata, other, word_counts, alone_counts):
    """'kata', 'other' or None for a look-alike pair at text[start:end]."""
    left_ch = text[start - 1] if start > 0 else ''
    right_ch = text[end] if end < len(text) else ''
    if is_katakana(left_ch) and is_katakana(right_ch):
        return 'kata'
    i, j = start, end
    while i > 0 and is_katakana(text[i - 1]):
        i -= 1
    while j < len(text) and is_katakana(text[j]):
        j += 1
    word = text[i:start] + kata + text[end:j]
    seen = word_counts.get(word, 0) - (text[start:end] == kata)  # not counting this place
    if len(word) >= 2 and seen > 0:
        return 'kata'  # ヘルメット elsewhere in the book
    rest_left, rest_right = text[i:start], text[end:j]
    if (len(rest_right) >= 2 and alone_counts.get(rest_right, 0)) or (len(rest_left) >= 2 and alone_counts.get(rest_left, 0)):
        return 'other'  # そこへリッキイ: リッキイ is a word of its own
    if kata in SAME_SHAPE and not is_katakana(left_ch) and not is_katakana(right_ch):
        return 'other'
    return None


def full_word(lines, index, start, end, reading):
    # The katakana word holding text[start:end] read as `reading`, joined with the
    # katakana that continues on the neighbouring line (モニター・ス|クリーン).
    line = lines[index]
    text = line['text']
    i, j = start, end
    while i > 0 and is_katakana(text[i - 1]):
        i -= 1
    while j < len(text) and is_katakana(text[j]):
        j += 1
    word = text[i:start] + reading + text[end:j]
    if not word or not all(is_katakana(ch) for ch in reading):
        return ''
    same_page = lambda k: 0 <= k < len(lines) and lines[k]['page'] == line['page']
    if j >= len(text.rstrip()) and line['joined'][1] and same_page(index + 1):
        following = lines[index + 1]['text'].lstrip()
        k = 0
        while k < len(following) and is_katakana(following[k]):
            k += 1
        word += following[:k]
    if i <= len(text) - len(text.lstrip()) and line['joined'][0] and same_page(index - 1):
        preceding = lines[index - 1]['text'].rstrip()
        k = len(preceding)
        while k > 0 and is_katakana(preceding[k - 1]):
            k -= 1
        word = preceding[k:] + word
    return word


def book_word_choice(lines, index, start, end, ndl, yomi, word_counts):
    # ('ndl' or 'yomitoku' or None, reason): the reading whose whole katakana word
    # occurs elsewhere in the book while the other one's does not.
    a, b = full_word(lines, index, start, end, ndl), full_word(lines, index, start, end, yomi)
    if len(a) < 2 or len(b) < 2 or a == b:
        return None, ''
    text = lines[index]['text']
    # The NDL word is in the text at this very place unless it runs over a line break.
    here = 1 if a in re.findall(r'[ァ-ヺー]{2,}', text) else 0
    ca, cb = word_counts.get(a, 0) - here, word_counts.get(b, 0)
    # Changing the text needs the word twice: a single other occurrence may be
    # another misreading (インタヴュウ/インタヴェウ once each).
    if cb >= 2 and ca <= 0:
        return 'yomitoku', f'{b}は本の中に{cb}回、{a}は0回'
    if ca > 0 and cb <= 0:
        return 'ndl', f'{a}は本の中に{ca}回、{b}は0回'
    return None, ''


PARTNERS = {'〈': '〉', '《': '》', '「': '」', '『': '』', '（': '）', '(': ')', '【': '】', '〔': '〕'}


def paired_bracket(lines, index, start, ndl):
    # NDL read a single bracket whose partner follows (or precedes) it on the
    # same line or the next (previous) line of the page: 〈シャ|ピアロン〉号.
    if len(ndl) != 1:
        return False
    line = lines[index]
    text = line['text']
    same_page = lambda k: 0 <= k < len(lines) and lines[k]['page'] == line['page']
    if ndl in PARTNERS:
        after = text[start + 1:] + (lines[index + 1]['text'] if same_page(index + 1) else '')
        return PARTNERS[ndl] in after
    opening = {v: k for k, v in PARTNERS.items()}.get(ndl)
    before = (lines[index - 1]['text'] if same_page(index - 1) else '') + text[:start]
    return bool(opening) and opening in before


BRACKET_MARKS = set('「」『』【】〔〕（）()《》〈〉［］[]')


def kind(x, y):
    if '"' in x:
        return 'quote'  # paired into 〝〟 per page after corrections
    if x != y and len(x) == len(y) and all(OLD_FORMS.get(a, a) == b for a, b in zip(x, y)):
        return 'old-form'
    if x != y and len(x) == len(y) and all(OLD_FORMS.get(b, b) == a for a, b in zip(x, y)):
        # NDL leans to the simplified form, so a traditional one from NDL is printed.
        return 'ndl-old-form'
    if len(x) == len(y) == 1 and x.translate(VOICING) == y:
        return 'voicing'
    if set(x + y) <= set('<>〈〉"〝〟＜＞'):
        return 'bracket'
    if len(x) == len(y) == 1 and x in BRACKET_MARKS and y in BRACKET_MARKS:
        return 'bracket-swap'  # 『 and 【 look alike in vertical print; a person decides
    if x and set(x) <= {'―'} and (not y or set(y) <= set('ーし|!」')):
        return 'dash'
    if len(x) == len(y) == 1 and unicodedata.normalize('NFKD', x)[0] == unicodedata.normalize('NFKD', y)[0]:
        return 'diacritic'
    if len(x) == len(y) and x.translate(SMALL) == y.translate(SMALL):
        return 'small-kana'
    if len(x) == len(y) == 1 and (LOOKALIKE.get(x) == y or LOOKALIKE.get(y) == x):
        return 'lookalike'
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
    word_corrections = load_corrections(args.word_corrections)
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
        items = json.loads(path.read_text())['contents'][0]
        joins = line_joins([item['text'] for item in items])
        for position, item in enumerate(items):
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
            text = apply_corrections(text, word_corrections.get(key, []), key)
            text = clean_text(text, vertical, glossary)
            if glossary:
                text = fix_variants(text, glossary, joined=joins[position])
            lines.append(dict(page=page, line_id=key[1], box=box_of(item['boundingBox']),
                              vertical=vertical, text=text, joined=joins[position]))
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


def resolve(verdict, item, text):
    """(to, start, end) for a human verdict. A row whose `from` is the whole
    snippet (left + NDL + right, with empty left/right) replaces the snippet.
    Older rows typed into the hunk box are read as the whole snippet when they
    start with the left context or end with the right one."""
    left, right = item['left'], item['right']
    start, end = item['start'], item['end']
    wide = (start - len(left), end + len(right))
    to = verdict['to']
    if not verdict['left'] and not verdict['right'] and verdict['from'] == left + item['ndl'] + right:
        return (to,) + wide
    if verdict['verdict'] == 'custom':
        if left and right and to.startswith(left) and to.endswith(right) and len(to) >= len(left) + len(right):
            return to[len(left):len(to) - len(right)], start, end
        if (left and to.startswith(left)) or (right and to.endswith(right)):
            return (to,) + wide
    return to, start, end


def flow(lines, item):
    # The end of the previous line | this line (hunk in bold) | the start of the next.
    esc = html.escape
    prev, nxt = neighbours(lines, item['index'])
    text = item['line']
    return (f'<span class="dim">{esc(prev[-12:])}</span>｜{esc(text[:item["start"]])}'
            f'<b>{esc(item["ndl"])}</b>{esc(text[item["end"]:])}｜<span class="dim">{esc(nxt[:12])}</span>')


def kinds(mine, theirs):
    # Name the script when one character differs only in it (ヘ/へ look the same),
    # and the form and code point of look-alike kanji (繋 U+7E4B / 繫 U+7E6B).
    if len(mine) == len(theirs) == 1 and script(mine) != script(theirs) and script(mine):
        return f'　（{mine}は{script(mine)}）'
    if len(mine) == len(theirs) == 1 and script(mine) == script(theirs) == '漢字':
        form = '旧字体' if mine in OLD_FORMS.values() else '新字体' if mine in OLD_FORMS else ''
        return f'　（{mine}は{form + "、" if form else ""}U+{ord(mine):04X}）'
    return ''


def line_shaped(item):
    return bool(set(item['ndl']) <= LINE_SHAPED and item['ndl']) or bool(set(item['yomitoku']) <= LINE_SHAPED and item['yomitoku'])


def neighbours(lines, index):
    # Text of the previous and next line on the same page ('' at page edges).
    page = lines[index]['page']
    prev = lines[index - 1]['text'] if index > 0 and lines[index - 1]['page'] == page else ''
    nxt = lines[index + 1]['text'] if index + 1 < len(lines) and lines[index + 1]['page'] == page else ''
    return prev, nxt


def at_edge(text, start, end):
    # 'start' or 'end' when the hunk is within two characters of a line edge,
    # where the sentence continues on the neighbouring line.
    lo, hi = len(text) - len(text.lstrip()), len(text.rstrip())
    if start - lo <= 1:
        return 'start'
    if hi - end <= 1:
        return 'end'
    return None


def at_join(line, start, end):
    """Whether the katakana run holding text[start:end] reaches a line edge that
    continues on the neighbouring line (line_joins)."""
    text = line['text']
    lo, hi = len(text) - len(text.lstrip()), len(text.rstrip())
    is_kana = lambda ch: '\u30a1' <= ch <= '\u30fa' or ch == 'ー'
    j = end
    while j < hi and is_kana(text[j]):
        j += 1
    i = start
    while i > lo and is_kana(text[i - 1]):
        i -= 1
    return (line['joined'][1] and j == hi) or (line['joined'][0] and i == lo)


WORD_CHARS = r'[ァ-ヺー・＝=―‐\-]'


def word_key(text, start, end, ndl, yomitoku):
    # A hunk inside a katakana word (シンゲン[ー→―]フー) is keyed by the whole
    # word, so a verdict holds wherever the word recurs, whatever follows it.
    if not (re.fullmatch(WORD_CHARS + '+', ndl) and re.fullmatch(WORD_CHARS + '*', yomitoku)):
        return None
    left = re.search(WORD_CHARS + '*$', text[:start]).group()
    right = re.match(WORD_CHARS + '*', text[end:]).group()
    if not (re.search('[ァ-ヺ]', left) and re.search('[ァ-ヺ]', right)):
        return None
    return left, ndl, yomitoku, right


def series_verdicts(dirs):
    # Word verdicts (word_key -> chosen readings) a person made in other books of the series.
    found = defaultdict(set)
    for directory in dirs:
        lines = {r['id']: r for r in read_tsv(directory / 'judge' / 'history.tsv')}
        for row in read_tsv(directory / 'judge-human.tsv'):
            seen = lines.get(row['id'])
            if row['verdict'] not in ('ndl', 'yomitoku') or not seen:
                continue
            context = seen['left'] + seen['ndl'] + seen['right']
            if seen['line'].count(context) != 1:
                continue
            start = seen['line'].find(context) + len(seen['left'])
            key = word_key(seen['line'], start, start + len(seen['ndl']), seen['ndl'], seen['yomitoku'])
            if key:
                found[key].add(row['to'])
    return found


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
details.group{{border-top:3px double #999;margin-top:1.5em;padding-top:.5em}} details.group>summary{{font-size:1.15em;cursor:pointer;padding:.4em 0}}
section.human h2::after{{content:"（確認済み）";color:#070}}
.ex{{display:flex;gap:10px;align-items:flex-start;margin:.5em 0}} .opts label{{margin-right:1em}}
table{{border-collapse:collapse}} td{{padding:2px 8px;border:1px solid #ccc;font-size:16px}}
tr.now td{{background:#e3f4e3;font-weight:bold}}
td.flow{{font-size:14px}} .dim{{color:#777}}
.done h2{{color:#777}} #out{{position:sticky;bottom:0;background:#f4f4f4;padding:.6em;border-top:2px solid #999}}
textarea{{width:100%;height:10em;font-family:monospace}}
</style>
<h1>{title}</h1>
<p><b>使い方</b>: 画面下の「次の未確認」（キーは n、戻るは p）で、まだ選んでいない箇所へ移れる。原本と違う箇所だけ選び直し（「自分で書く」は、横の欄の前後を含む文字列を正しく書き直す）、見終えたところで「ここまで確認済み」を押す（それより前の未確認が「今の読みでよい」になる）。
最後に「TSVをコピー」して保存する。確認済みの箇所は、作り直すと画面の最後に回る。</p>
<p>NDLOCR-LiteとYomiTokuで読みが割れ、ローカルLLM（{model}）が判断した箇所。各箇所の緑の行が、いま文字層に入っている読み。<b>反映済み</b>はYomiTokuの読みに置き換えた箇所。
原本画像と違うものだけ選び直せばよい。正しいと確かめた箇所は、その読みを選ぶと人の確認として記録される。選んだ結果は下のTSVにまとまるので、コピーして <code>{human}</code> に保存し、
<code>judge-ocr-disagreements.py --human</code> に渡して作り直す。人の判定は常にLLMより優先する。選択はこのブラウザにだけ一時保存される。</p>
<p>{summary}</p>
{sections}
<div id="out"><button id="prev">↑ 前の未確認（p）</button> <button id="next">次の未確認 ↓（n）</button> <label><input type="checkbox" id="advance" checked>選んだら次の未確認へ進む</label> <button id="savecmd">保存コマンドをコピー</button>（端末に貼って実行すると {human} に保存） <button id="copy">TSVだけコピー</button> <span id="status"></span><textarea id="tsv" readonly></textarea></div>
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
    if (v !== 'undecided') store[it.id] = {{v, custom}};
    document.getElementById('s' + i).classList.toggle('done', v !== 'undecided');
    if (v === 'undecided') return;
    if (v === 'custom') {{
      // The box holds the whole snippet (context included), so replace all of it.
      out.push([it.id, it.page, it.line_id, '', it.left + it.from + it.right, custom, '', v].join('\\t'));
      return;
    }}
    const to = v === 'ndl' ? it.from : v === 'yomitoku' ? it.yomitoku : it.now;
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
document.addEventListener('change', e => {{
  // A choice also goes to unanswered items with the same readings and context.
  const m = e.target.name && e.target.name.match(/^h(\\d+)$/);
  if (!m) return;
  const i = Number(m[1]);
  ITEMS.forEach((it, j) => {{
    if (j === i || it.sig !== ITEMS[i].sig) return;
    const cur = document.querySelector('input[name="h' + j + '"]:checked');
    if (cur && cur.value !== 'undecided') return;
    const el = document.querySelector('input[name="h' + j + '"][value="' + e.target.value + '"]');
    if (el) {{ el.checked = true; document.getElementById('c' + j).value = document.getElementById('c' + i).value; }}
  }});
  render();
}});
const COUNT = ITEMS.length;
const OPEN = i => {{ const el = document.querySelector('input[name="h' + i + '"]:checked'); return ITEMS[i].focus && (!el || el.value === 'undecided'); }};
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
document.querySelectorAll('button.upto').forEach(b => b.addEventListener('click', () => {{
  // Mark every unanswered item up to this one as checked with its current reading.
  for (let i = 0; i <= Number(b.dataset.n); i++) {{
    if (!document.querySelector('input[name="h' + i + '"]:checked') ||
        document.querySelector('input[name="h' + i + '"]:checked').value === 'undecided') {{
      document.querySelector('input[name="h' + i + '"][value="ok"]').checked = true;
    }}
  }}
  render();
}}));
document.addEventListener('input', render);
const SAVE_PATH = {save_path};
document.getElementById('savecmd').addEventListener('click', async () => {{
  // One click: a shell command with the TSV inside (base64, so tabs and
  // Japanese survive the terminal paste) that writes the file.
  const tsv = document.getElementById('tsv').value;
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(tsv)));
  const cmd = "echo '" + b64 + "' | base64 -d > " + SAVE_PATH;
  try {{ await navigator.clipboard.writeText(cmd); }}
  catch (e) {{ const area = document.getElementById('tsv'); area.value = cmd; area.select(); document.execCommand('copy'); render(); }}
  document.getElementById('status').textContent += '（保存コマンドをコピーした。端末に貼って実行）';
}});
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
    parser.add_argument('--word-corrections', type=Path, help='voicing corrections, as given to rebuild-text-layer.py')
    parser.add_argument('--vocabulary', type=Path,
                        help='TSV (word, count) of katakana words from finished books of the same series; '
                             'added to the book-word rule')
    parser.add_argument('--human', type=Path, help='TSV saved from the review page')
    parser.add_argument('--series', type=Path, action='append', default=[],
                        help="work directory of a reviewed book of the same series; its person's verdicts "
                             'on hunks inside katakana words apply here too (repeatable)')
    parser.add_argument('--max-hunks', type=int, default=4)
    parser.add_argument('--skip-pages', help="pages not judged, e.g. '312-322'")
    parser.add_argument('--skip-catalog', action='store_true',
                        help='also skip back-matter catalog pages (text_layer_utils.catalog_pages)')
    parser.add_argument('--url', default='http://127.0.0.1:8080/v1/chat/completions')
    parser.add_argument('--model', default='dahara1/gemma-4-12B-it-qat-UD-japanese-imatrix:Q4_K_XL')
    parser.add_argument('--prompt', choices=sorted(PROMPTS) + ['v1+v2'], default='v1+v2',
                        help='prompt version; v1+v2 applies v1 unless v2 answers NDL. '
                             'Answers are kept per version in history.tsv')
    parser.add_argument('--title', default='LLM判断のレビュー')
    args = parser.parse_args()

    ask = load_module('auto_variant_glossary', ROOT / 'auto-variant-glossary.py').ask
    crops = load_module('voicing_review', ROOT / 'build-voicing-review.py').crops
    glossary = load_glossary(args.glossary) if args.glossary else []
    skip = parse_pages(args.skip_pages) | (catalog_pages(args.ocr_dir, args.ocr_stem) if args.skip_catalog else set())
    lines = [line for line in load_lines(args, glossary) if line['page'] not in skip]
    if skip:
        print(f'pages not judged: {",".join(map(str, sorted(skip)))}')
    yomitoku = load_yomitoku(args.yomitoku_dir)
    args.output.mkdir(parents=True, exist_ok=True)
    history_path = args.output / 'history.tsv'
    done = {(r['id'], r['model'], r['prompt']): dict(r, page=int(r['page']), line_id=int(r['line_id']))
            for r in read_tsv(history_path)}
    human = {r['id']: r for r in read_tsv(args.human)}
    today = datetime.date.today().isoformat()

    items, counts = [], defaultdict(int)
    word_counts = Counter(w for line in lines for w in re.findall(r'[ァ-ヺー]{2,}', line['text']))
    alone_counts = standalone_counts(lines)
    if args.vocabulary:
        # Words from reviewed books of the series count as seen in this book.
        for row in read_tsv(args.vocabulary):
            word_counts[row['word']] += int(row['count'])
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
            text = line['text']
            if category == 'voicing' and at_join(line, start, end):
                # The voicing step skips a word running over the line break
                # (ピー|ル); the LLM decides it with the neighbouring lines (v2).
                category = 'joined-voicing'
            counts[category] += 1
            left, right = text[max(0, start - CONTEXT):start], text[end:end + CONTEXT]
            hunk_id = f'{line["page"]}:{line["line_id"]}:{left}[{frm}→{to}]{right}'
            if category == 'judge' and paired_bracket(lines, index, start, frm):
                # 〈シャピアロン〉号: YomiToku read 〉 as ソ and the LLM liked the
                # result; a bracket whose partner is on the line is printed.
                category = 'paired-bracket'
            if category in ('judge', 'lookalike', 'joined-voicing'):
                choice, reason = book_word_choice(lines, index, start, end, frm, to, word_counts)
                if choice:
                    items.append(dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left,
                                      ndl=frm, yomitoku=to, right=right, line=text, first='rule-word',
                                      second='rule-word', decision=choice, model='rule:book-word', prompt='',
                                      date=today, index=index, start=start, end=end, reason=reason))
                    counts['book-word'] += 1
                    continue
            if category == 'lookalike':
                kata, other = (frm, to) if is_katakana(frm) else (to, frm)
                choice = lookalike_choice(text, start, end, kata, other, word_counts, alone_counts)
                if choice:
                    pick = kata if choice == 'kata' else other
                    items.append(dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left,
                                      ndl=frm, yomitoku=to, right=right, line=text, first='rule-shape',
                                      second='rule-shape', decision='ndl' if pick == frm else 'yomitoku',
                                      model='rule:lookalike', prompt='', date=today,
                                      index=index, start=start, end=end))
                    continue
                if kata in SAME_SHAPE:
                    category = 'lookalike-undecided'  # the LLM only guesses here; keep NDL
                else:
                    category = 'judge'  # ロ/口, カ/力: context can decide
            if category == 'bracket-swap':
                items.append(dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left,
                                  ndl=frm, yomitoku=to, right=right, line=text, first='review',
                                  second='review', decision='split', model='rule:bracket-swap', prompt='',
                                  date=today, index=index, start=start, end=end))
                continue
            if category not in ('judge', 'old-form', 'joined-voicing'):
                # Rules keep NDL here, but a human verdict on the hunk still wins.
                if hunk_id in human:
                    items.append(dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left,
                                      ndl=frm, yomitoku=to, right=right, line=text, first='skip',
                                      second='skip', decision='ndl', model=f'rule:{category}', prompt='',
                                      date=today, index=index, start=start, end=end))
                continue
            if category == 'old-form':
                items.append(dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left, ndl=frm,
                                  yomitoku=to, right=right, line=text, first='rule', second='rule',
                                  decision='yomitoku', model='rule:old-form', prompt='', date=today,
                                  index=index, start=start, end=end))
                continue
            edge = at_edge(text, start, end)

            def asked(version):
                # At a line edge v1 also sees the line the sentence continues on.
                if version == 'v1' and edge:
                    version = 'v1-edge'
                record = done.get((hunk_id, args.model, version))
                if record is not None:
                    return record
                if version == 'v1':
                    mine, theirs = text, text[:start] + to + text[end:]
                elif version == 'v1-edge':
                    prev, nxt = neighbours(lines, index)
                    before = prev if edge == 'start' else ''
                    after = nxt if edge == 'end' else ''
                    mine = before + text + after
                    theirs = before + text[:start] + to + text[end:] + after
                else:
                    same_page = lambda i: 0 <= i < len(lines) and lines[i]['page'] == line['page']
                    before = lines[index - 1]['text'] if same_page(index - 1) else ''
                    after = lines[index + 1]['text'] if same_page(index + 1) else ''
                    mine = before + text[:start] + '【' + frm + '】' + text[end:] + after
                    theirs = before + text[:start] + '【' + to + '】' + text[end:] + after
                prompt = PROMPTS['v1' if version == 'v1-edge' else version]
                first = ask(args.url, args.model, prompt.format(a=mine, b=theirs))
                second = ask(args.url, args.model, prompt.format(a=theirs, b=mine))
                one = {'A': 'ndl', 'B': 'yomitoku'}.get(first)
                two = {'A': 'yomitoku', 'B': 'ndl'}.get(second)
                record = dict(id=hunk_id, page=line['page'], line_id=line['line_id'], left=left, ndl=frm,
                              yomitoku=to, right=right, line=text, first=first, second=second,
                              decision=one if one and one == two else 'split', model=args.model,
                              prompt=version, date=today)
                new_history.append(record)
                done[(hunk_id, args.model, version)] = record
                return record

            if category == 'joined-voicing':
                record = dict(asked('v2'))
            elif args.prompt == 'v1+v2':
                # v1 decides; v2 (context and caution) can veto a change to YomiToku.
                # On the Summer Door review this kept 40 of v1's 41 fixes and cut
                # its breakages from 6 to 2.
                one, two = asked('v1'), asked('v2')
                decision = one['decision']
                if decision == 'yomitoku' and two['decision'] == 'ndl':
                    decision = 'split'
                record = dict(one, decision=decision, prompt='v1+v2', first2=two['first'], second2=two['second'])
            else:
                record = asked(args.prompt)
            items.append(dict(record, index=index, start=start, end=end))

    # A human verdict that no longer matches a hunk (the text before it changed,
    # e.g. after a new voicing glossary) would be dropped silently; stop instead.
    orphans = sorted(set(human) - {item['id'] for item in items})
    # The same name or phrase recurs (フォーサイス＝スコット): a verdict on one hunk
    # applies to unreviewed hunks with the same readings and the same 4 characters
    # on each side, unless verdicts on such hunks disagree.
    signature = lambda it: (it['left'], it['ndl'], it['yomitoku'], it['right'])
    by_signature = defaultdict(set)
    for item in items:
        verdict = human.get(item['id'])
        if verdict:
            by_signature[signature(item)].add((verdict['verdict'], verdict['to'], verdict['from'], verdict['left'], verdict['right']))
    propagated = {}
    for item in items:
        found = by_signature.get(signature(item))
        if item['id'] not in human and found and len(found) == 1:
            v, to, frm, left, right = next(iter(found))
            propagated[item['id']] = dict(id=item['id'], page=item['page'], line_id=item['line_id'], left=left,
                                          to=to, right=right, verdict=v, propagated='yes', **{'from': frm})
    # A verdict on a hunk inside a katakana word applies wherever the word recurs
    # (シンゲン[ー→―]フー before は, の, や ...), here or in reviewed books of the series.
    by_word = series_verdicts(args.series)
    for item in items:
        verdict = human.get(item['id'])
        if verdict and verdict['verdict'] in ('ndl', 'yomitoku'):
            key = word_key(lines[item['index']]['text'], item['start'], item['end'], item['ndl'], item['yomitoku'])
            if key:
                by_word[key].add(verdict['to'])
    words_applied = 0
    for item in items:
        if item['id'] in human or item['id'] in propagated:
            continue
        found = by_word.get(word_key(lines[item['index']]['text'], item['start'], item['end'],
                                     item['ndl'], item['yomitoku']))
        if found and len(found) == 1 and (to := next(iter(found))) in (item['ndl'], item['yomitoku']):
            propagated[item['id']] = dict(id=item['id'], page=item['page'], line_id=item['line_id'],
                                          left=item['left'], to=to, right=item['right'], propagated='word',
                                          verdict='ndl' if to == item['ndl'] else 'yomitoku', **{'from': item['ndl']})
            words_applied += 1
    print(f'verdicts applied by word: {words_applied}')
    # A book sets one bracket style (its log entries in 【】, not 『』): a swap
    # decided by a person applies to the other swaps of the same two marks.
    by_marks = defaultdict(set)
    for item in items:
        verdict = human.get(item['id'])
        if verdict and item.get('model') == 'rule:bracket-swap' and verdict['verdict'] in ('ndl', 'yomitoku', 'ok'):
            by_marks[(item['ndl'], item['yomitoku'])].add(verdict['to'])
    for item in items:
        found = by_marks.get((item['ndl'], item['yomitoku']))
        if (item.get('model') == 'rule:bracket-swap' and item['id'] not in human
                and item['id'] not in propagated and found and len(found) == 1):
            propagated[item['id']] = dict(id=item['id'], page=item['page'], line_id=item['line_id'],
                                          left=item['left'], to=next(iter(found)), right=item['right'],
                                          verdict='ok', propagated='yes', **{'from': item['ndl']})
    if orphans:
        raise SystemExit(f'{len(orphans)} human verdicts match no hunk (text changed?): {orphans[:5]}')
    human.update(propagated)
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
            text = lines[item['index']]['text']
            to, start, end = resolve(verdict, item, text)
            item['human_snippet'] = text[item['start'] - len(item['left']):start] + to + text[end:item['end'] + len(item['right'])]
            if to != text[start:end]:
                corrections.append((dict(item, start=start, end=end), to, 'human'))
        elif item['decision'] == 'yomitoku':
            corrections.append((item, item['yomitoku'], 'rule' if item['first'] == 'rule' else 'llm'))
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

    # Split answers need a person; answers the LLM gave twice alike (and rule
    # decisions) are mostly right and go to a folded section; human-decided last.
    group = lambda it: 2 if it['id'] in human else 0 if it['decision'] == 'split' else 1
    order = {'yomitoku': 0, 'split': 1, 'ndl': 2}
    items.sort(key=lambda it: (group(it), order[it['decision']], it['page'], it['line_id']))
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
                if name == 'zoom' and line_shaped(item):
                    picture = picture.resize((picture.width * 2, picture.height * 2))  # 6x for thin marks
                picture.save(images / f'{n}-{name}.jpg', quality=85)
    document.close()

    sections, data = {0: [], 1: [], 2: []}, []
    labels = {'yomitoku': 'applied', 'split': 'split', 'ndl': 'kept'}
    for n, item in enumerate(items):
        verdict = human.get(item['id'])
        data.append({'id': item['id'], 'page': item['page'], 'line_id': item['line_id'], 'left': item['left'],
                     'from': item['ndl'], 'yomitoku': item['yomitoku'], 'right': item['right'],
                     'now': item['yomitoku'] if item['decision'] == 'yomitoku' else item['ndl'],
                     'human': {'v': verdict['verdict'], 'custom': item['human_snippet']} if verdict else None,
                     'focus': group(item) == 0,
                     'sig': '|'.join((item['left'], item['ndl'], item['yomitoku'], item['right']))})
        esc = html.escape
        # Prompt order: first asks A=NDL, B=YomiToku; second swaps them.
        said = lambda answer, a, b: {'A': a, 'B': b}.get(answer, '答えなし')
        first = said(item['first'], 'NDL', 'YomiToku')
        second = said(item['second'], 'YomiToku', 'NDL')
        reading = {'NDL': item['ndl'], 'YomiToku': item['yomitoku']}
        llm = (f'1回目: {esc(reading.get(first, ""))}（{first}）／'
               f'2回目（順序を入れ替え）: {esc(reading.get(second, ""))}（{second}）')
        if 'first2' in item:
            v2a = said(item['first2'], 'NDL', 'YomiToku')
            v2b = said(item['second2'], 'YomiToku', 'NDL')
            llm += (f'<br>第2版（前後の行つき）: {esc(reading.get(v2a, ""))}（{v2a}）／'
                    f'{esc(reading.get(v2b, ""))}（{v2b}）')
        if item['first'] == 'rule':
            llm = '聞いていない（旧字体はYomiTokuを採る規則）'
        elif item['first'] == 'rule-shape':
            llm = '聞いていない（形が同じ文字。前後の文字と、本の中に出てくる語で決めた）'
        elif item['first'] == 'rule-word':
            llm = f'聞いていない（本の中の語で決めた: {esc(item.get("reason", ""))}）'
        elif item['first'] == 'review':
            llm = '聞いていない（括弧の種類だけが違う。縦書きでは『と【が似ていて、文脈では決まらない）'
        elif item['first'] == 'skip':
            llm = f'聞いていない（規則でNDLのまま: {esc(item["model"][5:])}）'
        if verdict and verdict.get('propagated') == 'word':
            now, why = verdict['to'], '人の判定（同じ語の別の箇所か、シリーズの前の本での判定を当てた）'
        elif verdict and verdict.get('propagated'):
            now, why = verdict['to'], '人の判定（同じ形の箇所の判定を当てた）'
        elif verdict:
            now, why = verdict['to'], '人の判定'
        elif item['first'] == 'rule':
            now, why = item['yomitoku'], 'YomiTokuの読み。旧字体の規則'
        elif item['first'] == 'rule-word':
            chosen = item['yomitoku'] if item['decision'] == 'yomitoku' else item['ndl']
            now, why = chosen, f'{"YomiToku" if item["decision"] == "yomitoku" else "NDL"}の読み。本の中の語の規則'
        elif item['first'] == 'rule-shape':
            chosen = item['yomitoku'] if item['decision'] == 'yomitoku' else item['ndl']
            now, why = chosen, f'{"YomiToku" if item["decision"] == "yomitoku" else "NDL"}の読み。形が同じ文字の規則'
        elif item['decision'] == 'yomitoku':
            now, why = item['yomitoku'], 'YomiTokuの読み。LLMが2回とも選んだ'
        elif item['decision'] == 'split':
            now, why = item['ndl'], 'NDLの読み。LLMの答えが割れた（第2版の反対を含む）'
        else:
            now, why = item['ndl'], 'NDLの読み。LLMが2回とも選んだ'
        current = 'yomitoku' if item['decision'] == 'yomitoku' else 'ndl'
        mark = lambda v: '（反映中）' if v == current and not verdict else ''
        options = ''.join(f'<label><input type="radio" name="h{n}" value="{v}">{label}</label>'
                          for v, label in (('ndl', f'NDL「{esc(item["ndl"])}」{mark("ndl")}'),
                                           ('yomitoku', f'YomiToku「{esc(item["yomitoku"])}」{mark("yomitoku")}'),
                                           ('ok', '今の読みでよい'), ('custom', '自分で書く'),
                                           ('undecided', '未確認')))
        sections[group(item)].append(
            f'<section id="s{n}" class="{"human" if verdict else labels[item["decision"]]}"><h2>#{n + 1} {item["page"]}ページ・行{item["line_id"]}'
            f'{"【線・記号：6倍で表示】" if line_shaped(item) else ""}</h2>'
            f'<table><tr class="now"><td>文字層の今の読み</td><td>{esc(item["left"])}{esc(now)}{esc(item["right"])}（{why}）</td></tr>'
            f'<tr><td>NDL</td><td>{esc(item["left"])}<b>{esc(item["ndl"])}</b>{esc(item["right"])}{kinds(item["ndl"], item["yomitoku"])}</td></tr>'
            f'<tr><td>YomiToku</td><td>{esc(item["left"])}<b>{esc(item["yomitoku"])}</b>{esc(item["right"])}{kinds(item["yomitoku"], item["ndl"])}</td></tr>'
            f'<tr><td>LLM</td><td>{llm}</td></tr>'
            f'<tr><td>前後の行</td><td class="flow">{flow(lines, item)}</td></tr></table>'
            f'<div class="ex"><img src="img/{n}-thumb.jpg"><img src="img/{n}-line.jpg"><img src="img/{n}-zoom.jpg"></div>'
            f'<div class="opts">{options} <input id="c{n}" size="24" value="{esc(item["left"] + item["ndl"] + item["right"])}" '
            f'title="前後の文字も含めて、正しい文字列に書き直す"> '
            f'<button class="upto" data-n="{n}">ここまで確認済み</button> '
            f'<a href="#" class="go" data-n="{n}">次の未確認へ ↓</a></div></section>')
    sure = [i for i in items if group(i) == 1]
    page_sections = '\n'.join(sections[0])
    if sure:
        page_sections += (f'<details class="group"><summary>LLMが2回とも同じ答え、または規則で決めた箇所（{len(sure)}件。'
                          f'反映 {sum(i["decision"] == "yomitoku" for i in sure)}、NDLのまま {sum(i["decision"] == "ndl" for i in sure)}）'
                          f'— だいたい当たっているので、気になるときだけ開く</summary>\n' + '\n'.join(sections[1]) + '</details>')
    if sections[2]:
        page_sections += (f'<details class="group"><summary>人が判定済みの箇所（{len(sections[2])}件）</summary>\n'
                          + '\n'.join(sections[2]) + '</details>')
    summary = (f'確認が要るのは、LLMの答えが割れた {len(sections[0])}か所（下に並ぶ）。'
               f'判断した{len(items)}か所: 反映 {sum(i["decision"] == "yomitoku" for i in items)}、'
               f'割れた {sum(i["decision"] == "split" for i in items)}、NDLのまま {sum(i["decision"] == "ndl" for i in items)}。'
               f'人の判定 {sum(1 for i in items if i["id"] in human)}。規則で除外した差分: '
               + '、'.join(f'{k} {v}' for k, v in sorted(counts.items()) if k != 'judge'))
    (args.review / 'index.html').write_text(PAGE.format(
        title=html.escape(args.title), model=html.escape(args.model),
        human=html.escape(str(args.human.resolve() if args.human else 'work/eval/作業名-judge-human.tsv')),
        save_path=json.dumps(shlex.quote(str(args.human.resolve() if args.human else 'human.tsv'))), summary=summary,
        sections=page_sections, items=json.dumps(data, ensure_ascii=False), header='\\t'.join(HUMAN)),
        encoding='utf-8')
    print(summary)
    unreviewed = [i for i in items if i['id'] not in human]
    print(f'{len(new_history)} hunks asked now, {len(merged)} corrections: {args.output / "corrections.tsv"}')
    print(f'unreviewed LLM/rule decisions: {len(unreviewed)} '
          f'(applied {sum(i["decision"] == "yomitoku" for i in unreviewed)}); review: {args.review / "index.html"}')


if __name__ == '__main__':
    main()
