#!/usr/bin/python
"""Make readable copies of BOOKSCAN image PDFs from the reviewed catalog."""

import argparse
import csv
import os
import io
import re
import time
from pathlib import Path

import numpy as np
import pikepdf
from PIL import Image, ImageFilter
from scipy.ndimage import rotate as rotate_array


ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(os.environ.get('BOOKSCAN_PDF_DIR', ROOT / 'input/bookscan-downloads'))
DEST = ROOT / 'output/readable-pdf'
CATALOG = ROOT / 'data/bookscan-catalog.tsv'
NAMESPACE = 'https://local.bookscan-epub.example/metadata/1.0/'


def filename(book):
    name = book['title'] + (f" - {book['author']}" if book['author'] else '')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip(' .') + '.pdf'


def sample_image(raw):
    image = Image.open(io.BytesIO(raw))
    image.draft('RGB', (350, 500))
    image.thumbnail((350, 500))
    return np.asarray(image.convert('RGB')).astype(np.int16)


def classify(sample):
    brightness = sample.mean(axis=2)
    saturation = sample.max(axis=2) - sample.min(axis=2)
    dark = brightness < 150
    colored_ink = np.mean(dark & (saturation > 40))
    midtones = np.mean((brightness >= 150) & (brightness < 220))
    bright = sample[brightness > 180]
    paper = np.median(bright, axis=0) if len(bright) else np.array([0, 0, 0])
    text_page = 0.012 < np.mean(dark) < 0.16 and colored_ink < 0.018 and midtones < 0.18
    needs_cleaning = text_page and (min(paper) < 245 or max(paper) - min(paper) > 9)
    return text_page, needs_cleaning, paper


def skew_angle(sample):
    gray = (sample[:, :, 0] * 30 + sample[:, :, 1] * 59 + sample[:, :, 2] * 11) / 100
    ink = gray < 150
    h, w = ink.shape
    ink[: int(h * .06)] = False
    ink[int(h * .94):] = False
    ink[:, : int(w * .08)] = False
    ink[:, int(w * .92):] = False
    if ink.mean() < 0.01:
        return 0.0
    angles = np.arange(-1.5, 1.51, .25)
    scores = []
    for angle in angles:
        rotated = rotate_array(ink, angle, reshape=False, order=0, mode='constant', cval=False)
        columns = rotated.sum(axis=0)[int(w * .1): int(w * .9)]
        scores.append(float(np.var(columns)))
    best = int(np.argmax(scores))
    zero = len(angles) // 2
    if abs(angles[best]) < .5 or scores[best] < scores[zero] * 1.35:
        return 0.0
    return float(angles[best])


def clean_image(raw, paper, angle):
    image = Image.open(io.BytesIO(raw)).convert('RGB')
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
    output = io.BytesIO()
    image.save(output, 'JPEG', quality=90, subsampling=0)
    return output.getvalue(), image.mode


def process_page(page, index):
    images = page.get_images() if index >= 3 else {}
    if len(images) != 1:
        return 'preserved'
    image = next(iter(images.values()))
    if image.get('/Filter') != pikepdf.Name('/DCTDecode'):
        return 'preserved'
    raw = image.read_raw_bytes()
    sample = sample_image(raw)
    text_page, needs_cleaning, paper = classify(sample)
    if not text_page:
        return 'preserved'
    angle = skew_angle(sample)
    if not needs_cleaning and not angle:
        return 'preserved'
    new_raw, mode = clean_image(raw, paper if needs_cleaning else np.array([255, 255, 255]), angle)
    image.write(new_raw, filter=pikepdf.Name('/DCTDecode'))
    image.ColorSpace = pikepdf.Name('/DeviceGray' if mode == 'L' else '/DeviceRGB')
    image.BitsPerComponent = 8
    return 'deskewed_cleaned' if angle and needs_cleaning else ('deskewed' if angle else 'cleaned')


def add_metadata(pdf, book):
    pdf.docinfo['/Title'] = book['title']
    if book['author']:
        pdf.docinfo['/Author'] = book['author']
    if book['series']:
        pdf.docinfo['/Keywords'] = book['series']
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta.register_xml_namespace(NAMESPACE, 'bookscan')
        meta['dc:title'] = book['title']
        if book['author']:
            meta['dc:creator'] = [book['author']]
        if book['translator']:
            meta['dc:contributor'] = [book['translator']]
            meta['bookscan:Translator'] = book['translator']
        if book['series']:
            meta['bookscan:Series'] = book['series']
            meta['dc:relation'] = [book['series']]
        meta['bookscan:SourceFile'] = book['source']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='Original source PDF filename')
    parser.add_argument('--force', action='store_true', help='Rebuild an existing complete PDF')
    parser.add_argument('--max-pages', type=int, help='For a small visual trial only')
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error('shard index must be within shard count')
    with CATALOG.open(newline='') as handle:
        books = list(csv.DictReader(handle, delimiter='\t'))
    sources = {path.name for path in SOURCE.glob('*.pdf')}
    if len(books) != 83 or {book['source'] for book in books} != sources:
        raise SystemExit('Catalog and source PDF inventory differ')
    names = [filename(book).casefold() for book in books]
    if len(names) != len(set(names)):
        raise SystemExit('Output filename collision in catalog')
    DEST.mkdir(parents=True, exist_ok=True)
    log = DEST / 'conversion.tsv'
    if not log.exists():
        log.write_text('source\toutput\tpages\tcleaned\tdeskewed\tseconds\n')
    for number, book in enumerate(books, 1):
        if (number - 1) % args.shard_count != args.shard_index:
            continue
        if args.only and book['source'] != args.only:
            continue
        target = DEST / filename(book)
        source = SOURCE / book['source']
        if target.exists() and not args.max_pages and not args.force:
            with pikepdf.open(source) as original, pikepdf.open(target) as existing:
                if len(existing.pages) == len(original.pages):
                    print(f'{number}/{len(books)} already exists: {target.name}', flush=True)
                    continue
        start = time.monotonic()
        counts = {'cleaned': 0, 'deskewed': 0}
        with pikepdf.open(source) as pdf:
            pages = len(pdf.pages)
            for index, page in enumerate(pdf.pages[:args.max_pages] if args.max_pages else pdf.pages):
                result = process_page(page, index)
                for kind in counts:
                    counts[kind] += kind in result
            if args.max_pages:
                del pdf.pages[args.max_pages:]
            add_metadata(pdf, book)
            tmp = target.with_suffix('.partial.pdf')
            pdf.save(tmp, compress_streams=True)
        with pikepdf.open(tmp) as check:
            expected = min(pages, args.max_pages) if args.max_pages else pages
            if len(check.pages) != expected:
                raise RuntimeError(f'Page count changed: {source.name}')
        tmp.replace(target)
        duration = round(time.monotonic() - start, 1)
        print(f'{number}/{len(books)} {target.name}: {pages} pages, {counts}, {duration}s', flush=True)
        if not args.max_pages:
            with log.open('a') as handle:
                handle.write(f"{book['source']}\t{target.name}\t{pages}\t{counts['cleaned']}\t{counts['deskewed']}\t{duration}\n")


if __name__ == '__main__':
    main()
