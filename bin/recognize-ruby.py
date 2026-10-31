#!/usr/bin/env python3
"""Read NDLOCR-Lite ruby BLOCKs with its bundled PARSeq recognizers."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import yaml


ROOT = Path(__file__).resolve().parent.parent
NDL_SRC = ROOT / "vendor" / "ndlocr-lite" / "src"
sys.path.insert(0, str(NDL_SRC))
from parseq import PARSEQ  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("ndl_xml", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--padding", type=int, default=0)
    args = parser.parse_args()

    if args.padding < 0:
        parser.error("--padding must be nonnegative")
    image = cv2.imread(str(args.image))
    if image is None:
        parser.error(f"cannot read image: {args.image}")

    root = ET.parse(args.ndl_xml).getroot()
    blocks = [elem for elem in root.findall(".//BLOCK") if elem.get("TYPE") == "ルビ"]
    chars = yaml.safe_load((NDL_SRC / "config" / "NDLmoji.yaml").read_text())
    charset = list(chars["model"]["charset_train"])
    model_dir = NDL_SRC / "model"
    weights = {
        "30": "parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx",
        "50": "parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx",
        "100": "parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx",
    }
    recognizers = {
        name: PARSEQ(model_path=str(model_dir / filename), charlist=charset, device="cpu")
        for name, filename in weights.items()
    } if blocks else {}

    height, width = image.shape[:2]
    results = []
    for elem in blocks:
        x = int(elem.get("X"))
        y = int(elem.get("Y"))
        w = int(elem.get("WIDTH"))
        h = int(elem.get("HEIGHT"))
        left = max(0, x - args.padding)
        top = max(0, y - args.padding)
        right = min(width, x + w + args.padding)
        bottom = min(height, y + h + args.padding)
        crop = image[top:bottom, left:right]
        results.append({
            "box": [x, y, x + w, y + h],
            "crop_box": [left, top, right, bottom],
            "text": {name: recognizer.read(crop) for name, recognizer in recognizers.items()},
        })

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({
        "image": str(args.image),
        "ndl_xml": str(args.ndl_xml),
        "padding": args.padding,
        "rubies": results,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {args.output_json}: {len(results)} ruby blocks")


if __name__ == "__main__":
    main()
