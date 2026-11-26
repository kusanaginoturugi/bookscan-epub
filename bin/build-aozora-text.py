#!/usr/bin/env python3
"""Convert work/searchable/SLUG/lines.tsv into Aozora Bunko format text + cover image
for AozoraEpub3.

Uses line coordinates (y0 indentation, y1 line-end slack, x column gaps) to rebuild
paragraphs, scene breaks, and chapter headings (with sequential chapter-number repair).
"""

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

import pypdfium2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bin'))
from text_layer_utils import catalog_pages, parse_pages  # noqa: E402

OPEN_BRACKETS = set('「『（〝〈《【〔')
END_PUNCT = set('。！？?!」』）〟〉》】〕…―')
HEADING_WORDS = {'プロローグ', 'エピローグ', '解説', 'あとがき', '訳者あとがき', '登場人物', '序章', '終章'}


def load_catalog(source_pdf):
    with (ROOT / 'data/bookscan-catalog.tsv').open(newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            if row['source'] == source_pdf:
                return row
    return {}


PROTECT_RE = re.compile(r'(｜[^《]+《[^》]+》|※?［＃[^］]+］)')


def escape_aozora(text):
    """Escape literal Aozora ruby delimiters in body text."""
    return (text.replace('｜', '※［＃縦線、1-1-35］')
                .replace('《', '※［＃始め二重山括弧、1-1-52］')
                .replace('》', '※［＃終わり二重山括弧、1-1-53］'))


def _format_vertical_segment(text):
    """Adjust ASCII punctuation and Latin/digit runs for AozoraEpub3 vertical layout."""
    # Remove spaces between Japanese characters, keep spaces between Latin words.
    text = re.sub(r'(?<![A-Za-z0-9,.:;!?\'"\)])[ \u3000]+', '', text)
    text = re.sub(r'[ \u3000]+(?![A-Za-z0-9(\'"“])', '', text)
    # Single ? or ! -> full-width; 2-char combos like !? -> explicit tcy.
    text = re.sub(r'(?<![!?])\?(?![!?])', '？', text)
    text = re.sub(r'(?<![!?])!(?![!?])', '！', text)
    text = re.sub(r'(?<![!?])([!?]{2})(?![!?])', r'\1［＃「\1」は縦中横］', text)
    # Parentheses: keep half-width only when paired around ASCII inside Latin context.
    def _paren(m):
        inner = m.group(1)
        start, end = m.span()
        before = text[start - 1] if start > 0 else ''
        after = text[end] if end < len(text) else ''
        latin_ctx = (before.isascii() and (before.isalpha() or before == ' ')) or (
            after.isascii() and (after.isalpha() or after == ' '))
        if inner and inner.isascii() and re.search(r'[A-Za-z]', inner) and latin_ctx:
            return f'\x00{inner}\x01'
        return f'（{inner}）'
    text = re.sub(r'\(([^()]*)\)', _paren, text)
    text = text.replace('(', '（').replace(')', '）').replace('\x00', '(').replace('\x01', ')')
    text = re.sub(r'(?<![0-9A-Za-z])/(?![0-9A-Za-z])', '／', text)
    # Use U+2014 EM DASH instead of U+2015 HORIZONTAL BAR so vertical 'vert' glyph centers cleanly.
    text = text.replace('\u2015', '\u2014')
    # Full-width alphanumeric/code tokens without lowercase (e.g. MB-3076/K2/E, 730289C/EX, 5T, 6G).
    # Must run before 2-digit tcy and 1-digit full-width rules so 'K2/12' and 'A/5' stay intact.
    # Map '-' to U+2014 to match the centered vertical bar printed in Japanese paperbacks (e.g. ＭＢ―３０７６).
    fw_code = str.maketrans(
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-',
        'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９／\u2014',
    )
    def _code_to_fw(m):
        tok = m.group(1)
        if re.search(r'[A-Z]', tok) and re.search(r'[0-9/\-]', tok):
            return tok.translate(fw_code)
        return tok
    text = re.sub(r'(?<![0-9A-Za-z.\-])([A-Z0-9]+(?:[/-][A-Z0-9]+)*)(?![0-9A-Za-z]|[.\-][0-9A-Za-z])', _code_to_fw, text)
    # Standalone 2-digit numbers -> explicit tcy (合字).
    text = re.sub(r'(?<![0-9A-Za-z.\-])([0-9]{2})(?![0-9A-Za-z.\-])', r'\1［＃「\1」は縦中横］', text)
    # Full-width single-digit numbers when standalone in Japanese context.
    fw_digits = str.maketrans('0123456789', '０１２３４５６７８９')
    text = re.sub(r'(?<![0-9A-Za-z.\-])([0-9])(?![0-9A-Za-z.\-])', lambda m: m.group(1).translate(fw_digits), text)
    # Full-width short all-caps acronyms (1-4 chars, e.g. SF, P, UNSA, IDCC) not touching lowercase.
    fw_upper = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ')
    text = re.sub(r'(?<![A-Za-z0-9])([A-Z]{1,4})(?![A-Za-z0-9])', lambda m: m.group(1).translate(fw_upper), text)
    return text


def format_vertical_text(text):
    """Format body text while protecting Aozora ruby and annotation tags."""
    # Leading list markers like (1), (a), (b) -> explicit tate-chu-yoko followed by full-width space.
    m = re.match(r'^(\([0-9a-zA-Z]{1,2}\))[ \u3000]*', text)
    if m:
        marker = m.group(1)
        rest = text[m.end():]
        prefix = f'{marker}［＃「{marker}」は縦中横］' + ('　' if rest else '')
        parts = PROTECT_RE.split(rest)
        return prefix + ''.join(part if i % 2 else _format_vertical_segment(part) for i, part in enumerate(parts))
    parts = PROTECT_RE.split(text)
    return ''.join(part if i % 2 else _format_vertical_segment(part) for i, part in enumerate(parts))


def repair_chapter_numbers(texts):
    """Repair misread numeric chapter headings without overwriting valid ones when headings are missed.

    Finds the longest anchor subsequence of parsed numbers where
    j - i <= val[j] - val[i] <= j - i + 3 (allowing up to 3 skipped chapters between anchors),
    keeps every anchor's value (normalizing format like '3.' -> '3'), fills strictly between
    anchors only when the gap matches the index distance uniquely, and handles slices before
    the first / after the last anchor recursively if they form their own valid anchor chain
    (e.g. a part-reset '1,22,3,4' or a large skip '8,9,10').
    """
    n = len(texts)
    if n < 2:
        return list(texts), []
    parsed = []
    for t in texts:
        m = re.fullmatch(r'([0-9０-９]+)[.・]?', t)
        parsed.append(int(m.group(1)) if m else None)
    # Longest valid anchor chain: j - i <= val[j] - val[i] <= j - i + 3
    dp = [1 if v is not None else 0 for v in parsed]
    prev = [-1] * n
    for j in range(n):
        if parsed[j] is None:
            continue
        for i in range(j):
            if parsed[i] is not None and (j - i) <= parsed[j] - parsed[i] <= (j - i) + 3 and dp[i] + 1 > dp[j]:
                dp[j] = dp[i] + 1
                prev[j] = i
    best_end = max(range(n), key=lambda i: (dp[i], -i))
    if dp[best_end] < 2:
        return list(texts), []
    anchors = []
    cur = best_end
    while cur != -1:
        anchors.append(cur)
        cur = prev[cur]
    anchors.reverse()

    out = list(texts)
    for pos in anchors:
        out[pos] = str(parsed[pos])

    def _has_chain(sub):
        sub_out, _ = repair_chapter_numbers(sub)
        sub_parsed = [int(m.group(1)) if (m := re.fullmatch(r'([0-9０-９]+)[.・]?', s)) else None for s in sub]
        # True when the slice itself contains a valid >=2 anchor chain
        for j in range(len(sub_parsed)):
            if sub_parsed[j] is None:
                continue
            for i in range(j):
                if sub_parsed[i] is not None and (j - i) <= sub_parsed[j] - sub_parsed[i] <= (j - i) + 3:
                    return True, sub_out
        return False, None

    # Handle prefix before first anchor
    first = anchors[0]
    if first > 0:
        has_sub, sub_out = _has_chain(texts[:first])
        if has_sub:
            out[:first] = sub_out
        else:
            for pos in range(first):
                expected = parsed[first] - (first - pos)
                if expected >= 1:
                    out[pos] = str(expected)

    # Fill strictly between adjacent anchors only when gap matches index distance uniquely
    for a, b in zip(anchors, anchors[1:]):
        if parsed[b] - parsed[a] == b - a:
            for pos in range(a + 1, b):
                out[pos] = str(parsed[a] + (pos - a))

    # Handle suffix after last anchor
    last = anchors[-1]
    if last + 1 < n:
        has_sub, sub_out = _has_chain(texts[last + 1:])
        if has_sub:
            out[last + 1:] = sub_out
        else:
            for pos in range(last + 1, n):
                v = parsed[pos]
                expected = parsed[last] + (pos - last)
                # Preserve a plausible monotonic skip (e.g. 1..5, 10); fix non-monotonic or spike misreads
                if v is not None and parsed[last] < v <= expected + 10:
                    out[pos] = str(v)
                else:
                    out[pos] = str(expected)

    repaired = [(pos, texts[pos], out[pos]) for pos in range(n) if out[pos] != texts[pos]]
    return out, repaired


def is_chapter_heading(line, med_y0, med_dx, dx_before, dx_after, is_first_on_page, is_last_on_page):
    t = line['text'].strip()
    if not t or len(t) > 12:
        return False
    # Heading is indented down from body top and separated by large column gaps (or page edges).
    indented_down = line['y0'] >= med_y0 + 75
    has_after_gap = is_last_on_page or dx_after >= 1.5 * med_dx
    has_before_gap = is_first_on_page or dx_before >= 1.7 * med_dx
    if t in HEADING_WORDS and indented_down and (has_after_gap or is_first_on_page):
        return True
    # Chapter number (often misread as '11', '22', '3.', '80', '81' on 1-2 digit numbers)
    if indented_down and has_before_gap and has_after_gap and line['h'] <= 60 and re.fullmatch(r'[0-9０-９一二三四五六七八九十.・]+', t):
        return True
    return False


def is_colophon_page(lines):
    joined = ' '.join(l['text'] for l in lines)
    hits = sum(w in joined for w in ('検印', '発行', '印刷', '発行所', '定価', '訳者紹介', '装幀', '装丁'))
    return hits >= 2


def extract_cover(pdf_path, out_jpg):
    doc = pypdfium2.PdfDocument(str(pdf_path))
    img = doc[0].render(scale=200 / 72).to_pil().convert('RGB')
    img.save(out_jpg, quality=90)
    doc.close()


def apply_ruby(text, entries):
    """Insert Aozora ruby ｜base《ruby》 at the occurrence closest to each entry's char_pos,
    and escape literal Aozora delimiters outside inserted ruby tags."""
    if not entries:
        return escape_aozora(text)
    placements = []
    used = [False] * len(text)
    for char_pos, base, ruby, is_human in sorted(entries, key=lambda e: e[0]):
        if not is_human:
            # Trim leading hiragana on pure-hiragana ruby (e.g. てて屯/たむろ -> 屯/たむろ)
            if re.fullmatch(r'[ぁ-ゖー]+', ruby):
                while len(base) >= 2 and ('\u3041' <= base[0] <= '\u3096') and not ruby.startswith(base[0]):
                    base = base[1:]
            # Trim common trailing hiragana okurigana (e.g. 掻いた/かいた -> 掻/か) and stray trailing hiragana
            while len(base) >= 2 and len(ruby) >= 2 and ('\u3041' <= base[-1] <= '\u3096') and base[-1] == ruby[-1]:
                base, ruby = base[:-1], ruby[:-1]
            while len(base) >= 2 and ('\u3041' <= base[-1] <= '\u3096') and not ruby.endswith(base[-1]):
                base = base[:-1]
            # Skip likely misaligned rubies whose base has no kanji or Latin letter
            if not any(('\u4e00' <= c <= '\u9fff') or c in '々〆ヶ' or ('A' <= c <= 'Z') or ('a' <= c <= 'z') for c in base):
                continue
        best_idx, best_dist = None, 10**9
        start = 0
        while True:
            idx = text.find(base, start)
            if idx < 0:
                break
            if not any(used[idx:idx + len(base)]):
                d = abs(idx - char_pos)
                if d < best_dist:
                    best_dist = d
                    best_idx = idx
            start = idx + 1
        if best_idx is not None and best_dist <= 8:
            for k in range(best_idx, best_idx + len(base)):
                used[k] = True
            placements.append((best_idx, best_idx + len(base), base, ruby))
    out = []
    pos = 0
    for i0, i1, base, ruby in sorted(placements, key=lambda p: p[0]):
        out.append(escape_aozora(text[pos:i0]))
        out.append(f'｜{escape_aozora(base)}《{ruby}》')
        pos = i1
    out.append(escape_aozora(text[pos:]))
    return ''.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work_dir', type=Path, help='work/searchable/SLUG directory')
    parser.add_argument('output_txt', type=Path, help='output Aozora Bunko UTF-8 text file')
    parser.add_argument('--source', required=True, help='source PDF filename in data/bookscan-catalog.tsv')
    parser.add_argument('--cover-out', type=Path, help='save page 1 of source PDF as cover JPEG')
    parser.add_argument('--ruby', type=Path, help='ruby.tsv from bin/extract-ruby.py')
    parser.add_argument('--ruby-human', type=Path, help='ruby-human.tsv from bin/audit-ruby.py review page')
    parser.add_argument('--start-page', type=int, help='first PDF page to include')
    parser.add_argument('--end-page', type=int, help='last PDF page to include')
    args = parser.parse_args()

    ruby_human = {}
    if args.ruby_human and args.ruby_human.exists():
        with args.ruby_human.open(newline='') as handle:
            for r in csv.DictReader(handle, delimiter='\t'):
                ruby_human[r['id']] = r

    ruby_by_line = {}
    if args.ruby and args.ruby.exists():
        with args.ruby.open(newline='') as handle:
            for r in csv.DictReader(handle, delimiter='\t'):
                h = ruby_human.get(r.get('id', ''))
                if h:
                    if h['choice'] == 'drop':
                        continue
                    base, ruby, is_human = h['base'], h['ruby'], True
                else:
                    base, ruby, is_human = r.get('base', ''), r.get('ruby', ''), False
                if base and ruby:
                    ruby_by_line.setdefault((int(r['page']), int(r['line_id'])), []).append(
                        (int(r['char_pos']), base, ruby, is_human))

    meta = load_catalog(args.source)
    title = meta.get('title') or args.work_dir.name
    author = meta.get('author', '')
    translator = meta.get('translator', '')
    author_line = f'{author}　{translator}訳' if (author and translator) else (author or translator)

    ocr_dir = args.work_dir / 'ocr'
    ocr_stem = sorted(ocr_dir.glob('*.json'))[0].stem.rsplit('_', 1)[0]
    skip = catalog_pages(ocr_dir, ocr_stem)
    skip_file = args.work_dir / 'skip-pages.txt'
    if skip_file.exists():
        skip |= parse_pages(skip_file.read_text().strip())

    # Load all lines from lines.tsv
    by_page = {}
    ruby_applied = 0
    with (args.work_dir / 'lines.tsv').open(newline='') as handle:
        for r in csv.DictReader(handle, delimiter='\t'):
            p = int(r['page'])
            lid = int(r['line_id'])
            x0, y0, x1, y1 = (float(v) for v in r['box'].split(','))
            raw_text = r['final'].strip()
            entries = ruby_by_line.get((p, lid))
            if entries:
                text = apply_ruby(raw_text, entries)
                ruby_applied += text.count('《')
            else:
                text = escape_aozora(raw_text)
            by_page.setdefault(p, []).append({
                'page': p,
                'line_id': lid,
                'vertical': r['vertical'] == '1',
                'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                'w': x1 - x0, 'h': y1 - y0,
                'text': text,
                'raw_text': raw_text,
            })

    all_pages = sorted(by_page)
    # Estimate body geometry from middle pages
    mid_lo = all_pages[len(all_pages) // 10]
    mid_hi = all_pages[len(all_pages) * 9 // 10]
    full_y0, full_y1, full_h, pitches = [], [], [], []
    for p in range(mid_lo, mid_hi + 1):
        if p in skip:
            continue
        ls = [l for l in by_page.get(p, []) if l['vertical']]
        for l in ls:
            if l['h'] > 800:
                full_y0.append(l['y0'])
                full_y1.append(l['y1'])
                full_h.append(l['h'])
        for i in range(1, len(ls)):
            dx = ls[i - 1]['x0'] - ls[i]['x0']
            if 30 < dx < 100:
                pitches.append(dx)

    med_y0 = statistics.median(full_y0)
    med_y1 = statistics.median(full_y1)
    med_dx = statistics.median(pitches)
    char_h = statistics.median(full_h) / 40.0  # approx 1-char vertical pitch (~35px)

    # Auto-detect start/end pages if not given
    start_page = args.start_page
    if not start_page:
        for p in all_pages:
            ls = by_page[p]
            if any(l['text'] in ('プロローグ', '序章', '1', '１', '一') and l['y0'] >= med_y0 + 60 for l in ls):
                start_page = p
                break
        if not start_page:
            start_page = all_pages[0]

    end_page = args.end_page
    if not end_page:
        end_page = all_pages[-1]
        for p in reversed(all_pages):
            if p in skip or not by_page.get(p) or is_colophon_page(by_page[p]):
                end_page = p - 1
            else:
                break

    # First pass: collect ordered lines with headings & gaps
    stream = []
    chapter_num_indices = []
    for p in range(start_page, end_page + 1):
        if p in skip:
            continue
        ls = by_page.get(p, [])
        if not ls or is_colophon_page(ls):
            continue
        # Sort right-to-left (x0 descending) so horizontal chapter numbers sit in reading order
        ls_sorted = sorted(ls, key=lambda l: -l['x0'])
        # Estimate page-level non-indented top baseline (20th percentile of long vertical lines)
        v_tops = sorted(l['y0'] for l in ls_sorted if l['vertical'] and l['h'] > 600)
        page_y0 = v_tops[max(0, len(v_tops) // 5)] if v_tops else med_y0
        v_bots = sorted(l['y1'] for l in ls_sorted if l['vertical'] and l['h'] > 600)
        page_y1 = v_bots[min(len(v_bots) - 1, len(v_bots) * 4 // 5)] if v_bots else med_y1

        for i, l in enumerate(ls_sorted):
            if not l['text']:
                continue
            dx_before = (ls_sorted[i - 1]['x0'] - l['x0']) if i > 0 else 0
            dx_after = (l['x0'] - ls_sorted[i + 1]['x0']) if i + 1 < len(ls_sorted) else 0
            heading = is_chapter_heading(l, med_y0, med_dx, dx_before, dx_after, i == 0, i == len(ls_sorted) - 1)
            scene_break = (not heading) and (i > 0) and (dx_before >= 1.8 * med_dx) and (not stream or stream[-1]['type'] != 'heading')
            if heading:
                entry = {'type': 'heading', 'page': p, 'text': l['text'], 'numeric': bool(re.fullmatch(r'[0-9０-９.・]+', l['text']))}
                if entry['numeric']:
                    chapter_num_indices.append(len(stream))
                stream.append(entry)
            else:
                # Skip stray non-vertical single lines that look like page numbers at page edges
                if not l['vertical'] and len(l['text']) <= 4 and re.fullmatch(r'[0-9０-９]+', l['text']):
                    continue
                if scene_break:
                    stream.append({'type': 'blank', 'page': p})
                # Indentation relative to page top (account for slight tilt across columns by comparing to nearest non-indented)
                dy0 = l['y0'] - page_y0
                dy1 = l['y1'] - page_y1
                stream.append({
                    'type': 'line', 'page': p, 'text': l['text'],
                    'raw_first': l['raw_text'][0] if l['raw_text'] else '',
                    'raw_last': l['raw_text'][-1] if l['raw_text'] else '',
                    'vertical': l['vertical'], 'dy0': dy0, 'dy1': dy1,
                    'first_on_page': i == 0,
                })

    # Repair sequential numeric chapter headings (e.g. '11','22','3.','11','5.','66','7.','80','9.','10'..'24')
    if len(chapter_num_indices) >= 3:
        orig_nums = [stream[idx]['text'] for idx in chapter_num_indices]
        fixed_nums, repaired = repair_chapter_numbers(orig_nums)
        for idx, new in zip(chapter_num_indices, fixed_nums):
            stream[idx]['text'] = new
        if repaired:
            print(f'repaired chapter numbers ({len(repaired)}): ' +
                  ', '.join(f'p.{stream[chapter_num_indices[pos]]["page"]}: {old}->{new}'
                            for pos, old, new in repaired))

    # Second pass: assemble paragraphs
    out_lines = [title, author_line, '', '']
    buf = ''
    buf_indent = False
    prev_line = None

    def flush():
        nonlocal buf, buf_indent
        if buf:
            formatted = format_vertical_text(buf)
            out_lines.append(('　' + formatted) if buf_indent else formatted)
            buf = ''
            buf_indent = False

    for item in stream:
        if item['type'] == 'heading':
            flush()
            if len(out_lines) > 4:
                out_lines.append('［＃改ページ］')
            htext = item['text']
            if re.fullmatch(r'[0-9]{2}', htext):
                htext = f'{htext}［＃「{htext}」は縦中横］'
            elif re.fullmatch(r'[0-9]', htext):
                htext = htext.translate(str.maketrans('0123456789', '０１２３４５６７８９'))
            out_lines.append(f'［＃大見出し］{htext}［＃大見出し終わり］')
            out_lines.append('')
            prev_line = None
            continue
        if item['type'] == 'blank':
            flush()
            if out_lines and out_lines[-1] != '':
                out_lines.append('')
            prev_line = None
            continue

        t = item['text']
        raw_first = item['raw_first']
        starts_bracket = raw_first in OPEN_BRACKETS
        # Paragraph start heuristics:
        # 1. No previous line (after heading / blank)
        # 2. Previous line ended clearly short of bottom margin (slack >= 1.3 chars)
        # 3. Current line is indented down (dy0 >= 0.55 * char_h) and not a bracket
        # 4. Current line starts with dialogue bracket 「『 and previous line ended with end punctuation or short
        # 5. Horizontal block (e.g., bibliography list on p.310)
        new_para = False
        if prev_line is None:
            new_para = True
        elif not item['vertical'] or not prev_line['vertical']:
            new_para = True
        elif prev_line['dy1'] <= -1.3 * char_h:
            new_para = True
        elif (not starts_bracket) and item['dy0'] >= 0.55 * char_h:
            new_para = True
        elif raw_first in '「『' and (prev_line['raw_last'] in END_PUNCT or prev_line['dy1'] <= -0.8 * char_h):
            new_para = True

        if new_para:
            flush()
            # Add Japanese full-width indent for narrative paragraphs (not dialogue/bracket-led or horizontal lists)
            buf_indent = item['vertical'] and (not starts_bracket)
            buf = t
        else:
            # Join with previous line; preserve space between Latin words/digits except split acronyms or split numbers
            both_alnum = bool(buf and buf[-1].isascii() and buf[-1].isalnum() and raw_first.isascii() and raw_first.isalnum())
            split_acronym = bool(re.search(r'(?<![a-z])[A-Z]+$', buf) and 'A' <= raw_first <= 'Z')
            split_number = bool(buf and buf[-1].isdigit() and raw_first.isdigit())
            if both_alnum and not split_acronym and not split_number:
                buf += ' ' + t
            else:
                buf += t
        prev_line = item

    flush()
    args.output_txt.parent.mkdir(parents=True, exist_ok=True)
    args.output_txt.write_text('\n'.join(out_lines) + '\n', encoding='utf-8')

    if args.cover_out:
        pdf_path = ROOT / 'input/bookscan-downloads' / args.source
        if pdf_path.exists():
            args.cover_out.parent.mkdir(parents=True, exist_ok=True)
            extract_cover(pdf_path, args.cover_out)

    headings = [it['text'] for it in stream if it['type'] == 'heading']
    print(f'wrote {args.output_txt}: pages {start_page}-{end_page}, {len(out_lines)} lines, '
          f'{len(headings)} headings, {ruby_applied} rubies')


if __name__ == '__main__':
    main()
