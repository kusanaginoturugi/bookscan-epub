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
