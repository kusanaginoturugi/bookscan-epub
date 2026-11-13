#!/usr/bin/env python3
"""Set PDF binding direction for readers that honor ViewerPreferences."""

import argparse
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--direction", choices=("rtl", "ltr"), required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

    writer = PdfWriter(clone_from=PdfReader(args.source))
    root = writer._root_object
    current = root.get("/ViewerPreferences")
    preferences = DictionaryObject()
    if current is not None:
        preferences.update(current.get_object())
    preferences[NameObject("/Direction")] = NameObject(
        "/R2L" if args.direction == "rtl" else "/L2R"
    )
    root[NameObject("/ViewerPreferences")] = writer._add_object(preferences)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as handle:
        writer.write(handle)

    result = PdfReader(args.output)
    actual = result.trailer["/Root"]["/ViewerPreferences"]["/Direction"]
    if actual != preferences["/Direction"] or len(result.pages) != len(writer.pages):
        raise RuntimeError("PDF direction or page count changed unexpectedly")
    print(f"{args.output}: {len(result.pages)} pages, /Direction {actual}")


if __name__ == "__main__":
    main()
