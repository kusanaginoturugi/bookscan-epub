#!/usr/bin/env python3
"""Add small margin notes to an OCR-backed reading PDF without changing its pages."""

import argparse
from io import BytesIO
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link, Text
from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject,
                           FloatObject, NameObject, TextStringObject)
from reportlab.pdfgen import canvas


ICON_SIZE = 22  # PDF viewers may render text icons at their own fixed size.
ICON_MARGIN = 5
LINK_SIZE = 20
LINK_GAP = 3


def ocr_line(ocr_dir, stem, page_number, line_id):
    path = ocr_dir / f"{stem}_{page_number:05}.json"
    data = json.loads(path.read_text())
    matches = [line for group in data["contents"] for line in group
               if line["id"] == line_id]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one line with id {line_id}")
    return matches[0]


def note_rect(page, line, term, side):
    content = line["text"]
    if content.count(term) != 1:
        raise ValueError(f"term {term!r} is missing or ambiguous in {content!r}")
    # Text annotation icons render larger than their rectangle in some viewers.
    # Keep them above the first text line rather than beside a vertical column.
    y = float(page.mediabox.top) - ICON_MARGIN - ICON_SIZE
    if side == "left":
        x = float(page.mediabox.left) + ICON_MARGIN
    elif side == "right":
        x = float(page.mediabox.right) - ICON_MARGIN - ICON_SIZE
    else:
        raise ValueError(f"unknown margin side: {side}")
    return (x, y, x + ICON_SIZE, y + ICON_SIZE)


def source_link_rect(note_rect, side):
    if side == "left":
        x = note_rect[2] + LINK_GAP
    else:
        x = note_rect[0] - LINK_GAP - LINK_SIZE
    y = note_rect[1] + (ICON_SIZE - LINK_SIZE) / 2
    return (x, y, x + LINK_SIZE, y + LINK_SIZE)


def draw_margin_marks(page, note_rect, link_rect):
    """Draw quiet marks; separate annotations provide hover and click actions."""
    nx, ny, _, _ = note_rect
    x0, y0, _, _ = link_rect
    packet = BytesIO()
    drawing = canvas.Canvas(packet, pagesize=(float(page.mediabox.width),
                                               float(page.mediabox.height)))
    drawing.setStrokeColorRGB(0.77, 0.77, 0.77)
    drawing.setLineWidth(0.7)
    drawing.roundRect(nx + 3, ny + 8, 16, 11, 3, stroke=1, fill=0)
    drawing.line(nx + 7, ny + 8, nx + 5, ny + 5)
    drawing.line(nx + 5, ny + 5, nx + 11, ny + 8)
    drawing.roundRect(x0 + 0.5, y0 + 0.5, LINK_SIZE - 1, LINK_SIZE - 1,
                      3, stroke=1, fill=0)
    drawing.line(x0 + 5, y0 + 5, x0 + 14, y0 + 14)
    drawing.line(x0 + 9, y0 + 14, x0 + 14, y0 + 14)
    drawing.line(x0 + 14, y0 + 9, x0 + 14, y0 + 14)
    drawing.save()
    page.merge_page(PdfReader(BytesIO(packet.getvalue())).pages[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("notes", type=Path)
    parser.add_argument("ocr_dir", type=Path)
    parser.add_argument("ocr_stem")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    reader = PdfReader(args.source)
    writer = PdfWriter(clone_from=reader)
    notes = json.loads(args.notes.read_text())
    seen = set()
    count = 0
    for note in notes:
        for location in note["locations"]:
            number = location["page"]
            if not 1 <= number <= len(writer.pages):
                raise ValueError(f"page out of range: {number}")
            key = (number, location["line_id"])
            if key in seen:
                raise ValueError(f"duplicate note location: {key}")
            seen.add(key)
            line = ocr_line(args.ocr_dir, args.ocr_stem, number, location["line_id"])
            page = writer.pages[number - 1]
            rect = note_rect(page, line, location["term"], location["side"])
            body = f"{note['summary']}\n\n出典: {note['source']}"
            annotation = Text(rect=rect, text=body, open=False)
            annotation[NameObject("/T")] = TextStringObject(note["title"])
            # Firefox renders /Comment with its own hardcoded yellow SVG.
            # /NoIcon keeps the hover target while our gray mark stays visible.
            annotation[NameObject("/Name")] = NameObject("/NoIcon")
            annotation[NameObject("/C")] = ArrayObject([FloatObject(0.9)] * 3)
            writer.add_annotation(page_number=number - 1, annotation=annotation)
            link_rect = source_link_rect(rect, location["side"])
            draw_margin_marks(page, rect, link_rect)
            link = Link(rect=link_rect, url=note["source"])
            # PDF.js reads /NewWindow for /GoToR, but not for /URI.
            link[NameObject("/A")] = DictionaryObject({
                NameObject("/S"): NameObject("/GoToR"),
                NameObject("/F"): TextStringObject(note["source"]),
                NameObject("/NewWindow"): BooleanObject(True),
            })
            link[NameObject("/Contents")] = TextStringObject(
                f"{note['title']}の出典を開く"
            )
            writer.add_annotation(page_number=number - 1, annotation=link)
            count += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(".partial.pdf")
    with partial.open("wb") as handle:
        writer.write(handle)
    check = PdfReader(partial)
    if len(check.pages) != len(reader.pages):
        raise ValueError("page count changed")
    for number in {location["page"] for note in notes for location in note["locations"]}:
        annotations = [item.get_object() for item in
                       check.pages[number - 1].get("/Annots", [])]
        if len(annotations) != 2:
            raise ValueError(f"page {number}: note or source link is missing")
        if annotations[0].get("/Name") != "/NoIcon":
            raise ValueError(f"page {number}: Firefox note icon is visible")
        action = annotations[1].get("/A", {})
        if action.get("/S") != "/GoToR" or not action.get("/NewWindow"):
            raise ValueError(f"page {number}: source link will reuse the tab")
    partial.replace(args.output)
    print(f"{args.output}: {count} margin notes and source links on {len(check.pages)} pages")


if __name__ == "__main__":
    main()
