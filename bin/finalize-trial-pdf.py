#!/usr/bin/env python3
"""Restore metadata and verify pages after NDLOCR-Lite adds a PDF text layer."""

import argparse
import csv
import hashlib
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def image_digests(page):
    return [hashlib.sha256(image.data).digest() for image in page.images]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sanitized', type=Path)
    parser.add_argument('ocr_pdf', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--catalog-source', help='original PDF filename in data/bookscan-catalog.tsv')
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'output already exists: {args.output}')
    source = PdfReader(args.sanitized)
    ocr = PdfReader(args.ocr_pdf)
    if len(source.pages) != len(ocr.pages):
        raise ValueError('page count differs')
    chars = 0
    for index, (before, after) in enumerate(zip(source.pages, ocr.pages), 1):
        if (before.mediabox != after.mediabox or
                before.cropbox != after.cropbox or
                image_digests(before) != image_digests(after)):
            raise ValueError(f'page {index}: image or page box differs')
        chars += len(after.extract_text())
    if not chars:
        raise ValueError('no text was extracted from the OCR PDF')
    writer = PdfWriter(clone_from=ocr)
    metadata = source.metadata or {}
    info = {str(key): str(value) for key, value in metadata.items() if value is not None}
    if args.catalog_source:
        catalog = Path(__file__).resolve().parent.parent / 'data/bookscan-catalog.tsv'
        with catalog.open(newline='') as handle:
            book = next((row for row in csv.DictReader(handle, delimiter='\t')
                         if row['source'] == args.catalog_source), None)
        if book is None:
            raise ValueError(f'not in catalog: {args.catalog_source}')
        info.update({'/Title': book['title'], '/Author': book['author'],
                     '/Translator': book['translator'], '/Series': book['series'],
                     '/SourceFilename': args.catalog_source})
    writer.add_metadata(info)
    # Open as page-fit odd-page spreads with page-by-page scrolling.
    writer.page_layout = '/TwoPageLeft'
    writer.open_destination = writer.pages[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix('.partial.pdf')
    with partial.open('wb') as handle:
        writer.write(handle)
    result = PdfReader(partial)
    if len(result.pages) != len(source.pages) or result.metadata.title != metadata.title:
        raise ValueError('final PDF page count or title differs')
    if result.page_layout != '/TwoPageLeft' or result.trailer['/Root']['/OpenAction'][1] != '/Fit':
        raise ValueError('final PDF view settings are missing')
    partial.replace(args.output)
    print(f'{args.output}: {len(result.pages)} pages, {chars} extracted characters')


if __name__ == '__main__':
    main()
