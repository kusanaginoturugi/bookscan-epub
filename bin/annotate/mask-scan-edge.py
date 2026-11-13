#!/usr/bin/env python3
"""Cover scan-edge marks on selected PDF pages without touching OCR layers."""

import argparse
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    pages = parser.add_mutually_exclusive_group(required=True)
    pages.add_argument("--page", type=int, help="one-based PDF page")
    pages.add_argument("--even-pages", metavar="FIRST-LAST",
                       help="inclusive range of even PDF pages")
    parser.add_argument("--side", choices=("left", "right"), required=True)
    parser.add_argument("--pixels", type=int, required=True,
                        help="width of the cover in source-image pixels")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if args.pixels < 1:
        parser.error("--pixels must be positive")

    reader = PdfReader(args.source)
    if args.page is not None:
        numbers = [args.page]
    else:
        try:
            first, last = map(int, args.even_pages.split("-"))
        except ValueError:
            parser.error("--even-pages must be FIRST-LAST")
        if first % 2 or last % 2 or first > last:
            parser.error("--even-pages needs an ascending range of even pages")
        numbers = range(first, last + 1, 2)
    if not numbers or min(numbers) < 1 or max(numbers) > len(reader.pages):
        parser.error("page range is out of bounds")
    writer = PdfWriter(clone_from=reader)
    from PIL import Image
    for number in numbers:
        page = writer.pages[number - 1]
        if len(page.images) != 1:
            raise ValueError(f"page {number}: expected exactly one page image")
        image = Image.open(BytesIO(page.images[0].data))
        width = float(page.mediabox.width) * args.pixels / image.width
        x = (float(page.mediabox.left) if args.side == "left" else
             float(page.mediabox.right) - width)
        packet = BytesIO()
        drawing = canvas.Canvas(packet, pagesize=(float(page.mediabox.width),
                                                   float(page.mediabox.height)))
        drawing.setFillColorRGB(1, 1, 1)
        drawing.rect(x, float(page.mediabox.bottom), width,
                     float(page.mediabox.height), stroke=0, fill=1)
        drawing.save()
        page.merge_page(PdfReader(BytesIO(packet.getvalue())).pages[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as handle:
        writer.write(handle)
    print(f"{args.output}: covered {args.pixels}px on {len(numbers)} pages, {args.side} edge")


if __name__ == "__main__":
    main()
