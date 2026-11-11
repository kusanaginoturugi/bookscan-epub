#!/usr/bin/env python
"""Rerun only the NDLOCR-Lite text recognizer on the deskew study pages.

Run with .venv-ndl/bin/python; NDLOCR_SRC overrides the NDLOCR-Lite source
(default vendor/ndlocr-lite/src). The saved line boxes in
work/deskew-study are reused, so line geometry and pixel perturbations can be
varied without rerunning the detector. Output stays in work/ocr-sensitivity and
is not committed.
"""

import argparse
import csv
import importlib.util
import io
import json
import math
import random
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import os

import pypdfium2
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
STUDY = ROOT / 'work/deskew-study'
NDL_SRC = Path(os.environ.get('NDLOCR_SRC', ROOT / 'vendor/ndlocr-lite/src'))
RENDER_DPI = 300
CONDITIONS = {
    'original': ('original.pdf', 'original-ocr-control', 'original'),
    'reencoded': ('reencoded.pdf', 'reencoded-ocr', 'reencoded'),
    'rotated': ('rotated.pdf', 'rotated-ocr', 'rotated'),
}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


study = load_module('deskew_study', ROOT / 'bin/deskew-study.py')
sys.path.insert(0, str(NDL_SRC))
import ocr  # noqa: E402


def recognizers(device='cpu'):
    args = SimpleNamespace(rec_classes=str(NDL_SRC / 'config/NDLmoji.yaml'),
                           device=device, enable_tcy=False)
    model = NDL_SRC / 'model'
    models = [ocr.get_recognizer(args, weights_path=str(model / name)) for name in (
        'parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx',
        'parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx',
        'parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx')]
    # NDLOCR-Lite only prints a warning when CUDA is missing and falls back to
    # the CPU, whose readings differ on about 2% of lines. Refuse instead.
    if device == 'cuda' and any('CUDAExecutionProvider' not in m.session.get_providers() for m in models):
        raise RuntimeError('CUDAExecutionProvider is unavailable; install onnxruntime-gpu[cuda,cudnn]')
    return models


def render(pdf):
    # Same rendering call as ocr.py so the crops match the saved OCR input.
    document = pypdfium2.PdfDocument(str(pdf))
    pages = []
    for index in range(len(document)):
        image = next(iter(document.render(pypdfium2.PdfBitmap.to_pil, page_indices=[index],
                                          scale=RENDER_DPI / 72)))
        pages.append(np.array(image.convert('RGB')))
    return pages


def load_lines(path):
    lines = []
    for item in ET.parse(path).iter('LINE'):
        lines.append({'x': int(item.get('X')), 'y': int(item.get('Y')),
                      'w': int(item.get('WIDTH')), 'h': int(item.get('HEIGHT')),
                      # ocr.py uses 100 when the detector gave no count (small lines).
                      'count': float(item.get('PRED_CHAR_CNT') or 100),
                      'text': item.get('STRING')})
    return lines


def crop(image, x, y, w, h):
    height, width = image.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    return image[y0:min(height, int(y) + int(h)), x0:min(width, int(x) + int(w)), :]


def pad_to_width(image, width):
    extra = width - image.shape[1]
    if extra <= 0:
        return image
    edges = np.concatenate([image[:, 0, :], image[:, -1, :]])
    background = np.median(edges, axis=0).astype(np.uint8)
    left = extra // 2
    padded = np.empty((image.shape[0], width, 3), dtype=np.uint8)
    padded[:] = background
    padded[:, left:left + image.shape[1], :] = image
    return padded


def recognize(models, crops):
    # crops: list of (key, image, pred_char_cnt); process_cascade keeps the
    # 30/50/100-character escalation of ocr.py.
    lines = [ocr.RecogLine(image, index, count) for index, (_, image, count) in enumerate(crops)]
    texts = ocr.process_cascade(lines, *models, is_cascade=True)
    return {key: text for (key, _, _), text in zip(crops, texts)}


def jpeg(image, quality):
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, 'JPEG', quality=quality)
    return np.array(Image.open(io.BytesIO(buffer.getvalue())).convert('RGB'))


def shift(image, dx, dy):
    pil = Image.fromarray(image)
    return np.array(pil.transform(pil.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy),
                                  resample=Image.Resampling.BILINEAR, fillcolor=(255, 255, 255)))


def rotate(image, degrees):
    return np.array(Image.fromarray(image).rotate(degrees, resample=Image.Resampling.BICUBIC,
                                                  fillcolor=(255, 255, 255)))


def noise(image, seed):
    values = image.astype(np.float32) + np.random.default_rng(seed).normal(0, 1, image.shape)
    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


def perturbations():
    # Nuisance changes that should not matter to a reader. Each is applied the
    # same way to the original and rotated pages.
    items = [('baseline', None, None)]
    for seed in range(10):
        items.append((f'jitter{seed}', None, seed))
    for quality in (80, 90, 95):
        items.append((f'jpeg{quality}', lambda image, q=quality: jpeg(image, q), None))
    items.append(('shift_x0.5', lambda image: shift(image, 0.5, 0), None))
    items.append(('shift_y0.5', lambda image: shift(image, 0, 0.5), None))
    items.append(('rotate+0.1', lambda image: rotate(image, 0.1), None))
    items.append(('rotate-0.1', lambda image: rotate(image, -0.1), None))
    for seed in (1, 2):
        items.append((f'noise{seed}', lambda image, s=seed: noise(image, s), None))
    return items


def jittered(line, seed, key):
    rng = random.Random(f'{seed}-{key}')
    left, right, top, bottom = (rng.randint(-2, 2) for _ in range(4))
    return (line['x'] + left, line['y'] + top,
            line['w'] - left + right, line['h'] - top + bottom)


def pad_sweep(output, models, images, lines, gold, source_of, extras=(0, 4, 8, 14, 20, 30)):
    # Widen each vertical crop with page background on both sides, combined with
    # the same box jitter draws, to see whether extra margin helps consistently.
    path = output / 'pad-sweep.tsv'
    if path.exists():
        raise FileExistsError(f'{path} exists; move it before rerunning')
    rows = []
    for condition in ('original', 'rotated'):
        for extra in extras:
            for seed in [None, *range(10)]:
                crops = []
                for page in source_of:
                    image = images[condition][page - 1]
                    for line_id, line in enumerate(lines[condition][page]):
                        if (source_of[page], line_id) not in gold:
                            continue
                        box = (jittered(line, seed, (page, line_id)) if seed is not None else
                               (line['x'], line['y'], line['w'], line['h']))
                        image_crop = crop(image, *box)
                        if line['h'] > line['w']:
                            image_crop = pad_to_width(image_crop, image_crop.shape[1] + extra)
                        crops.append(((source_of[page], line_id), image_crop, line['count']))
                texts = recognize(models, crops)
                edits = sum(study.edit_distance(gold[key], study.normalized(text))
                            for key, text in texts.items())
                rows.append([condition, extra, 'baseline' if seed is None else f'jitter{seed}', edits])
            values = [row[3] for row in rows if row[0] == condition and row[1] == extra]
            print(f'{condition} +{extra}px: baseline {values[0]}, jitter min {min(values[1:])} '
                  f'median {statistics.median(values[1:])} max {max(values[1:])}', flush=True)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['condition', 'extra_px', 'variant', 'gold_edits'])
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=Path('work/ocr-sensitivity'))
    parser.add_argument('--pad-sweep', action='store_true',
                        help='score gold lines with extra background margin and box jitter')
    args = parser.parse_args()
    if not args.pad_sweep and (args.output / 'variants.tsv').exists():
        raise FileExistsError(f'{args.output}/variants.tsv exists; move it before rerunning')
    args.output.mkdir(parents=True, exist_ok=True)

    with (STUDY / 'pages.tsv').open(newline='') as handle:
        pages = list(csv.DictReader(handle, delimiter='\t'))
    with (STUDY / 'gold.tsv').open(newline='') as handle:
        gold = {(int(row['source_page']), int(row['line_id'])): study.normalized(row['expected'])
                for row in csv.DictReader(handle, delimiter='\t')}
    source_of = {int(row['study_page']): int(row['source_page']) for row in pages}
    angle_of = {int(row['study_page']): float(row['angle_deg']) for row in pages}

    models = recognizers()
    images, lines = {}, {}
    for condition, (pdf, folder, stem) in CONDITIONS.items():
        images[condition] = render(STUDY / pdf)
        lines[condition] = {page: load_lines(STUDY / folder / f'{stem}_{page:05}.xml')
                            for page in source_of}
    if args.pad_sweep:
        pad_sweep(args.output, models, images, lines, gold, source_of)
        return

    # Line ids must describe the same column in every condition.
    for page in source_of:
        counts = {condition: len(lines[condition][page]) for condition in CONDITIONS}
        if len(set(counts.values())) != 1:
            raise ValueError(f'study page {page}: line counts differ {counts}')
        height, width = images['original'][page - 1].shape[:2]
        angle = math.radians(angle_of[page])
        for line_id, (a, b) in enumerate(zip(lines['original'][page], lines['rotated'][page])):
            ox, oy = a['x'] + a['w'] / 2 - width / 2, a['y'] + a['h'] / 2 - height / 2
            tx = width / 2 + math.cos(angle) * ox + math.sin(angle) * oy
            ty = height / 2 - math.sin(angle) * ox + math.cos(angle) * oy
            distance = math.hypot(tx - b['x'] - b['w'] / 2, ty - b['y'] - b['h'] / 2)
            if distance > 10:
                print(f'warning: study page {page} line {line_id} centers {distance:.1f}px apart')

    rows, outputs = [], {}

    def run(condition, variant, crops):
        started = time.time()
        texts = recognize(models, crops)
        for (page, line_id), text in texts.items():
            key = (source_of[page], line_id)
            reference = gold.get(key)
            edits = study.edit_distance(reference, study.normalized(text)) if reference else ''
            rows.append([condition, variant, page, key[0], line_id, text, edits])
            outputs[(condition, variant, key)] = text
        print(f'{condition}/{variant}: {len(crops)} lines, {time.time() - started:.1f}s', flush=True)

    # 1. Replication: saved boxes on each condition's own rendering.
    replication = {}
    for condition in CONDITIONS:
        crops = []
        for page in source_of:
            image = images[condition][page - 1]
            for line_id, line in enumerate(lines[condition][page]):
                crops.append(((page, line_id), crop(image, line['x'], line['y'], line['w'], line['h']),
                              line['count']))
        run(condition, 'baseline', crops)
        same = sum(study.normalized(outputs[(condition, 'baseline', (source_of[page], line_id))]) ==
                   study.normalized(line['text'])
                   for page in source_of for line_id, line in enumerate(lines[condition][page]))
        replication[condition] = {'lines': len(crops), 'matches_saved_ocr': same}
        print(f'replication {condition}: {same}/{len(crops)} match saved OCR', flush=True)
    if any(item['lines'] != item['matches_saved_ocr'] for item in replication.values()):
        (args.output / 'summary.json').write_text(json.dumps({'replication': replication}, indent=2))
        raise SystemExit('replication failed; recognizer-only results would not describe ocr.py')

    # 2. Geometry: change only the crop width around the same column.
    geometry = {'rotated_pad_background': [], 'rotated_wide_real': [], 'original_narrow': []}
    for page in source_of:
        original, rotated = images['original'][page - 1], images['rotated'][page - 1]
        for line_id, (a, b) in enumerate(zip(lines['original'][page], lines['rotated'][page])):
            key = (page, line_id)
            tight = crop(rotated, b['x'], b['y'], b['w'], b['h'])
            if b['h'] > b['w']:
                geometry['rotated_pad_background'].append((key, pad_to_width(tight, a['w']), b['count']))
                cx = b['x'] + b['w'] / 2
                geometry['rotated_wide_real'].append(
                    (key, crop(rotated, round(cx - a['w'] / 2), b['y'], a['w'], b['h']), b['count']))
                cx = a['x'] + a['w'] / 2
                geometry['original_narrow'].append(
                    (key, crop(original, round(cx - b['w'] / 2), a['y'], b['w'], a['h']), a['count']))
            else:
                for name, image, line in (('rotated_pad_background', rotated, b),
                                          ('rotated_wide_real', rotated, b),
                                          ('original_narrow', original, a)):
                    geometry[name].append((key, crop(image, line['x'], line['y'], line['w'], line['h']),
                                           line['count']))
    for name, crops in geometry.items():
        condition, variant = name.split('_', 1)
        run(condition, variant, crops)

    # 3. Nuisance perturbations, identical for original and rotated pages.
    nuisance = perturbations()
    for condition in ('original', 'rotated'):
        for variant, transform, seed in nuisance[1:]:
            crops = []
            for page in source_of:
                image = images[condition][page - 1]
                if transform:
                    image = transform(image)
                for line_id, line in enumerate(lines[condition][page]):
                    box = (jittered(line, seed, (page, line_id)) if seed is not None else
                           (line['x'], line['y'], line['w'], line['h']))
                    crops.append(((page, line_id), crop(image, *box), line['count']))
            run(condition, variant, crops)

    with (args.output / 'variants.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        writer.writerow(['condition', 'variant', 'study_page', 'source_page', 'line_id', 'text', 'gold_edits'])
        writer.writerows(rows)

    def gold_edits(condition, variant):
        return sum(study.edit_distance(reference, study.normalized(outputs[(condition, variant, key)]))
                   for key, reference in gold.items())

    all_keys = [(source_of[page], line_id) for page in source_of
                for line_id in range(len(lines['original'][page]))]
    summary = {'replication': replication, 'gold_lines': len(gold),
               'gold_chars': sum(len(text) for text in gold.values()),
               'geometry': {f'{condition}/{variant}': gold_edits(condition, variant) for condition, variant in
                            (('original', 'baseline'), ('reencoded', 'baseline'), ('rotated', 'baseline'),
                             ('rotated', 'pad_background'), ('rotated', 'wide_real'),
                             ('original', 'narrow'))},
               'perturbations': {}}
    variants = [variant for variant, _, _ in nuisance]
    for condition in ('original', 'rotated'):
        edits = {variant: gold_edits(condition, variant) for variant in variants}
        unstable = {key for key in all_keys
                    if len({study.normalized(outputs[(condition, variant, key)]) for variant in variants}) > 1}
        unstable_gold = [key for key in gold if key in unstable]
        stable_gold = [key for key in gold if key not in unstable]

        def wrong(keys):
            return sum(study.edit_distance(gold[key], study.normalized(outputs[(condition, 'baseline', key)])) > 0
                       for key in keys)
        values = list(edits.values())
        summary['perturbations'][condition] = {
            'gold_edits': edits, 'min': min(values), 'median': statistics.median(values),
            'max': max(values), 'unstable_lines': len(unstable), 'lines': len(all_keys),
            'unstable_gold_lines': len(unstable_gold), 'unstable_gold_wrong_at_baseline': wrong(unstable_gold),
            'stable_gold_lines': len(stable_gold), 'stable_gold_wrong_at_baseline': wrong(stable_gold)}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    with (args.output / 'gold-lines.tsv').open('w', newline='') as handle:
        writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
        columns = [('original', 'baseline'), ('reencoded', 'baseline'), ('rotated', 'baseline'),
                   ('rotated', 'pad_background'), ('rotated', 'wide_real'), ('original', 'narrow')]
        writer.writerow(['source_page', 'line_id', 'expected'] +
                        [f'{c}/{v}' for c, v in columns] + [f'{c}/{v} edits' for c, v in columns])
        for key, reference in gold.items():
            texts = [study.normalized(outputs[(c, v, key)]) for c, v in columns]
            writer.writerow([*key, reference, *texts] +
                            [study.edit_distance(reference, text) for text in texts])
    print(json.dumps({k: v for k, v in summary.items() if k != 'perturbations'}, ensure_ascii=False))
    for condition, item in summary['perturbations'].items():
        print(condition, {k: v for k, v in item.items() if k != 'gold_edits'})


if __name__ == '__main__':
    main()
