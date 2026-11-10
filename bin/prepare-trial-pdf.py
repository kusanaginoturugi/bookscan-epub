#!/usr/bin/env python3
"""Prepare one image PDF with the existing paper cleanup for a searchable-PDF trial.

Run with the Python environment in .venv-ndl. This uses
pypdf because pikepdf is not available in that environment.
"""

import argparse
import csv
import os
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter
from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / 'data/bookscan-catalog.tsv'
SOURCE = Path(os.environ.get('BOOKSCAN_PDF_DIR', ROOT / 'input/bookscan-downloads'))


def classify(image):
    sample = image.copy()
    sample.thumbnail((350, 500))
    rgb = np.asarray(sample.convert('RGB'), dtype=np.int16)
    brightness = rgb.mean(axis=2)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    dark = brightness < 150
    colored_ink = np.mean(dark & (saturation > 40))
    midtones = np.mean((brightness >= 150) & (brightness < 220))
    bright = rgb[brightness > 180]
    paper = np.median(bright, axis=0) if len(bright) else np.array([0, 0, 0])
    text_page = 0.012 < np.mean(dark) < 0.16 and colored_ink < 0.018 and midtones < 0.18
    needs_cleaning = text_page and (min(paper) < 245 or max(paper) - min(paper) > 9)
    return sample, text_page, needs_cleaning, paper


def skew_angle(sample):
    rgb = np.asarray(sample.convert('RGB'), dtype=np.int16)
    gray = (rgb[:, :, 0] * 30 + rgb[:, :, 1] * 59 + rgb[:, :, 2] * 11) / 100
    ink = (gray < 150).astype(np.uint8)
    h, w = ink.shape
    ink[:int(h * .06)] = 0
    ink[int(h * .94):] = 0
    ink[:, :int(w * .08)] = 0
    ink[:, int(w * .92):] = 0
    if ink.mean() < 0.01:
        return 0.0
    angles = np.arange(-1.5, 1.51, .25)
    scores = []
    for angle in angles:
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), float(angle), 1)
        rotated = cv2.warpAffine(ink, matrix, (w, h), flags=cv2.INTER_NEAREST)
        columns = rotated.sum(axis=0)[int(w * .1):int(w * .9)]
        scores.append(float(np.var(columns)))
    best = int(np.argmax(scores))
    zero = len(angles) // 2
    if abs(angles[best]) < .5 or scores[best] < scores[zero] * 1.35:
        return 0.0
    return float(angles[best])


def clean_image(image, paper, angle):
    image = image.convert('RGB')
    if min(paper) < 245 or max(paper) - min(paper) > 9:
        gray = image.convert('L')
        background = gray.filter(ImageFilter.GaussianBlur(radius=35))
        difference = np.asarray(background, dtype=np.int16) - np.asarray(gray, dtype=np.int16)
        strength = np.clip((difference - 25) * 2, 0, 255).astype(np.uint8)
        cleaned = 255 - strength
        cleaned[cleaned > 225] = 255
        image = Image.fromarray(cleaned, 'L')
    if angle:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Original PDF filename from the catalog')
    parser.add_argument('output', type=Path)
    parser.add_argument('--max-pages', type=int, help='Trial with the first N pages')
    parser.add_argument('--no-deskew', action='store_true',
                        help='keep image geometry unchanged so OCR can run on the original first')
    parser.add_argument('--angles', type=Path,
                        help='write page and rotation angle (degrees, counterclockwise) of deskewed pages')
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error('--max-pages must be positive')
    if args.output.exists():
        parser.error(f'output already exists: {args.output}')
    with CATALOG.open(newline='') as handle:
        books = {row['source']: row for row in csv.DictReader(handle, delimiter='\t')}
    if args.source not in books:
        parser.error(f'not in catalog: {args.source}')
    source = SOURCE / args.source
    book = books[args.source]
    reader = PdfReader(source)
    writer = PdfWriter()
    for page in reader.pages[:args.max_pages]:
        writer.add_page(page)
    writer.add_metadata({'/Title': book['title'], '/Author': book['author'],
                         '/Translator': book['translator'], '/Series': book['series'],
                         '/SourceFilename': args.source})
    angles = []
    counts = {'cleaned': 0, 'deskewed': 0, 'deskew_candidates': 0, 'preserved': 0}
    for index, page in enumerate(writer.pages):
        images = page.images
        if index < 3 or len(images) != 1:
            counts['preserved'] += 1
            continue
        image = Image.open(io.BytesIO(images[0].data))
        sample, text_page, needs_cleaning, paper = classify(image)
        if not text_page:
            counts['preserved'] += 1
            continue
        angle = skew_angle(sample)
        if angle:
            counts['deskew_candidates'] += 1
        if args.no_deskew:
            angle = 0.0
        if not needs_cleaning and not angle:
            counts['preserved'] += 1
            continue
        updated = clean_image(image, paper if needs_cleaning else np.array([255] * 3), angle)
        images[0].replace(updated, quality=90, subsampling=0)
        counts['cleaned'] += bool(needs_cleaning)
        counts['deskewed'] += bool(angle)
        if angle:
            angles.append((index + 1, angle))
        if (index + 1) % 25 == 0:
            print(f'processed {index + 1}/{len(writer.pages)} pages', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix('.partial.pdf')
    with partial.open('wb') as handle:
        writer.write(handle)
    if len(PdfReader(partial).pages) != len(writer.pages):
        raise RuntimeError('page count changed')
    if args.angles:
        with args.angles.open('w', newline='') as handle:
            out = csv.writer(handle, delimiter='\t', lineterminator='\n')
            out.writerow(['page', 'angle_deg'])
            out.writerows(angles)
    partial.replace(args.output)
    print(f'{args.output}: {len(writer.pages)} pages, {counts}', flush=True)


if __name__ == '__main__':
    main()
