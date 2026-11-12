"""Join only image-verified glossary words split by OCR spaces."""

import csv
import re


def load_glossary(path):
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    terms = [row['term'] for row in rows if row.get('verified') == 'yes']
    if len(terms) != len(set(terms)) or any(len(term) < 2 for term in terms):
        raise ValueError('glossary has duplicate or too-short verified terms')
    return sorted(terms, key=len, reverse=True)


def clean_text(text, is_vertical, glossary=()):
    if not is_vertical or not glossary:
        return text
    for term in glossary:
        pattern = r'[ \u3000]+'.join(map(re.escape, term))
        text = re.sub(pattern, term, text)
    return text


def fix_dashes(text):
    # NDLOCR-Lite often reads a vertical two-em dash as ASCII hyphens; keep
    # hyphens next to Latin letters or digits such as M-47.
    return re.sub(r'(?<![0-9A-Za-z])-+(?![0-9A-Za-z])', '―', text)


BRACKETS = str.maketrans({'<': '〈', '＜': '〈', '>': '〉', '＞': '〉'})


def fix_brackets(text):
    # NDLOCR-Lite reads vertical 〈〉 as ASCII or full-width angle brackets.
    return text.translate(BRACKETS)


def fix_quotes(texts):
    """Turn ASCII double quotes in one page's vertical lines into 〝〟.

    texts are in reading order. Quote marks, including 〝〟 that OCR already got
    right, must alternate open/close; a page whose marks do not pair up (odd
    count, or a 〝 or 〟 out of turn) is returned unchanged. Returns the texts and
    the number of quotes replaced (None when the page was left alone).
    """
    marks = [ch for text in texts for ch in text if ch in '"〝〟']
    if '"' not in marks:
        return texts, 0
    if len(marks) % 2 or any(ch != '"' and ch != '〝〟'[i % 2] for i, ch in enumerate(marks)):
        return texts, None
    index = 0
    result = []
    for text in texts:
        chars = []
        for ch in text:
            if ch in '"〝〟':
                ch = '〝〟'[index % 2]
                index += 1
            chars.append(ch)
        result.append(''.join(chars))
    return result, marks.count('"')


VOICING = str.maketrans('バビブベボパピプペポ', 'パピプペポバビブベボ')


KATAKANA = re.compile(r'[ァ-ヴー]')


def line_joins(texts):
    """[(joined_to_previous, joined_to_next)] for the OCR lines of a page in
    reading order. A katakana word may run over the line break (パター|ン), so a
    line ending in katakana before one starting with katakana counts as joined;
    the fragment at either side is then not a word of its own."""
    texts = [t.strip() for t in texts]
    out = []
    for i, text in enumerate(texts):
        prev = texts[i - 1] if i > 0 else ''
        nxt = texts[i + 1] if i + 1 < len(texts) else ''
        out.append((bool(text and prev and KATAKANA.match(text[0]) and KATAKANA.match(prev[-1])),
                    bool(text and nxt and KATAKANA.match(text[-1]) and KATAKANA.match(nxt[0]))))
    return out


def fix_variants(text, glossary, counts=None, joined=(False, False)):
    # OCR confuses dakuten and handakuten (ビ/ピ). Replace a reading that
    # differs from a verified term by one such kana, unless it is part of a
    # longer katakana word, is itself a verified term, or is a fragment of a
    # word running over the line break (joined, from line_joins).
    lo, hi = len(text) - len(text.lstrip()), len(text.rstrip())
    for term in glossary:
        for index, char in enumerate(term):
            swapped = char.translate(VOICING)
            variant = term[:index] + swapped + term[index + 1:]
            if swapped == char or variant in glossary:
                continue
            pattern = rf'(?<![ァ-ヴー]){re.escape(variant)}(?![ァ-ヴー])'
            found = 0

            def replace(match):
                nonlocal found
                if (joined[0] and match.start() == lo) or (joined[1] and match.end() == hi):
                    return match.group()
                found += 1
                return term
            text = re.sub(pattern, replace, text)
            if found and counts is not None:
                counts[(variant, term)] += found
    return text


def parse_pages(spec):
    """{page numbers} from '312-322,330'."""
    pages = set()
    for part in filter(None, (p.strip() for p in (spec or '').split(','))):
        first, _, last = part.partition('-')
        pages.update(range(int(first), int(last or first) + 1))
    return pages


def catalog_pages(ocr_dir, ocr_stem, tail=20, min_lines=30, max_average=16):
    """Back-matter catalog pages: among the last `tail` pages, those with many
    short lines (a publisher's list of titles), unlike body text (about 18
    lines of 40 characters per page in these paperbacks)."""
    import json
    paths = sorted(ocr_dir.glob(f'{ocr_stem}_*.json'))
    found = set()
    for path in paths[-tail:]:
        texts = [item['text'] for item in json.loads(path.read_text())['contents'][0]]
        if len(texts) >= min_lines and sum(map(len, texts)) / len(texts) < max_average:
            found.add(int(path.stem.rsplit('_', 1)[1]))
    return found
