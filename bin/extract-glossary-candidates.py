#!/usr/bin/env python3
"""Extract repeated katakana terms from a searchable PDF for glossary review."""

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader


TERM = re.compile(r'[ァ-ヴー・]{3,}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--minimum', type=int, default=2)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    counts = Counter()
    pages = defaultdict(set)
    reader = PdfReader(args.pdf)
    for page, entry in enumerate(reader.pages, 1):
        for term in TERM.findall(entry.extract_text()):
            if set(term) == {'ー'} or set(term) == {'・'}:
                continue
            counts[term] += 1
            pages[term].add(page)
    terms = [(term, count) for term, count in counts.items() if count >= args.minimum]
    terms.sort(key=lambda pair: (-pair[1], pair[0]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t')
        writer.writerow(['term', 'occurrences', 'pages', 'verified'])
        for term, count in terms[:args.limit]:
            writer.writerow([term, count, ','.join(map(str, sorted(pages[term])[:12])), ''])
    print(f'{len(reader.pages)} pages, {len(terms)} repeated candidates; wrote {min(args.limit, len(terms))}')


if __name__ == '__main__':
    main()
