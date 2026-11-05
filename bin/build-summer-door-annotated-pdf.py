#!/usr/bin/env python3
"""Combine the three reading PDFs and add one first-appearance glossary note per page."""

import argparse
import csv
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from reportlab.pdfgen import canvas


PDFS = [
    '1793868466_夏への扉 - 注釈試作 薄色右開き 001-120.pdf',
    '1793868514_夏への扉 - 注釈試作 薄色右開き 121-240.pdf',
    '1793868531_夏への扉 - 注釈試作 薄色右開き 241-356.pdf',
]
ALREADY_ANNOTATED = {'修正第四条', 'ホイスラー', '母の肖像'}


def draw_mark(page, rect):
    packet = BytesIO()
    drawing = canvas.Canvas(packet, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
    x, y, _, _ = rect
    drawing.setStrokeColorRGB(0.77, 0.77, 0.77)
    drawing.setLineWidth(0.7)
    drawing.roundRect(x + 3, y + 8, 16, 11, 3, stroke=1, fill=0)
    drawing.line(x + 7, y + 8, x + 5, y + 5)
    drawing.line(x + 5, y + 5, x + 11, y + 8)
    drawing.save()
    page.merge_page(PdfReader(BytesIO(packet.getvalue())).pages[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('glossary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'output already exists: {args.output}')
    root = Path(__file__).resolve().parents[1]
    sources = [PdfReader(root / 'output' / name) for name in PDFS]
    writer = PdfWriter()
    for source in sources:
        writer.append(source, import_outline=False)
    if len(writer.pages) != 356:
        raise ValueError(f'expected 356 pages, got {len(writer.pages)}')

    grouped = defaultdict(list)
    rows = list(csv.DictReader(args.glossary.open(), delimiter='\t'))
    if len(rows) != 234:
        raise ValueError(f'expected 234 terms, got {len(rows)}')
    for row in rows:
        number = int(row['first_pdf_page'])
        term = row['term']
        if term not in writer.pages[number - 1].extract_text().replace('\n', ''):
            raise ValueError(f'{number}: term missing from PDF: {term}')
        if not row['note'].strip():
            raise ValueError(f'{number}: note missing: {term}')
        if term not in ALREADY_ANNOTATED:
            grouped[number].append(f"{term}：{row['note']}")

    for number, items in grouped.items():
        page = writer.pages[number - 1]
        width = float(page.mediabox.width)
        top = float(page.mediabox.top)
        x = (width - 22) / 2
        rect = (x, top - 27, x + 22, top - 5)
        note = Text(rect=rect, text='\n'.join(items), open=False)
        note[NameObject('/T')] = TextStringObject(f'用語メモ（{len(items)}語）')
        note[NameObject('/Name')] = NameObject('/NoIcon')
        note[NameObject('/C')] = ArrayObject([FloatObject(0.9)] * 3)
        writer.add_annotation(page_number=number - 1, annotation=note)
        draw_mark(page, rect)

    writer.add_metadata({'/Title': '夏への扉（用語注釈版）', '/Author': 'ロバート・A・ハインライン'})
    prefs = DictionaryObject({NameObject('/Direction'): NameObject('/R2L')})
    writer._root_object[NameObject('/ViewerPreferences')] = writer._add_object(prefs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('wb') as stream:
        writer.write(stream)

    result = PdfReader(args.output)
    if len(result.pages) != 356:
        raise ValueError('page count changed')
    if result.trailer['/Root']['/ViewerPreferences']['/Direction'] != '/R2L':
        raise ValueError('binding direction changed')
    for number, items in grouped.items():
        annotations = [item.get_object() for item in result.pages[number - 1].get('/Annots', [])]
        if not any(annotation.get('/T') == f'用語メモ（{len(items)}語）' for annotation in annotations):
            raise ValueError(f'page {number}: glossary annotation missing')
    print(f'{args.output}: {len(rows)} terms, {len(grouped)} glossary annotations, 356 pages')


if __name__ == '__main__':
    main()
