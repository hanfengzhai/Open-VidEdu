#!/usr/bin/env python3
"""Extract all text from WebVTT, split into one sentence per line, write to full_subtitle.txt."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    vtt_path = Path(__file__).parent / "plasticity_lecture_subtitles.txt"
    out_path = Path(__file__).parent / "full_subtitle.txt"
    if len(sys.argv) >= 2:
        vtt_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        out_path = Path(sys.argv[2])

    content = vtt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\n+", content)
    all_text: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block or block.upper() == "WEBVTT":
            continue
        lines = block.split("\n")
        i = 0
        if lines and re.match(r"^\d+$", lines[0].strip()):
            i = 1
        if i >= len(lines):
            continue
        if re.match(r"\d+:\d+:\d+\.?\d*\s*-->\s*\d+:\d+:\d+\.?\d*", lines[i].strip()):
            text = "\n".join(lines[i + 1 :]).strip()
            if text:
                all_text.append(text)

    # Join all cue text with space, then split into sentences (., !, ?)
    full = " ".join(all_text)
    # Normalize whitespace
    full = re.sub(r"\s+", " ", full).strip()
    # Split on sentence boundaries; keep delimiter attached to previous sentence
    sentences = re.split(r"(?<=[.!?])\s+", full)
    lines_out = []
    for s in sentences:
        s = s.strip()
        if s:
            lines_out.append(s)

    out_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines_out)} sentences to {out_path}")


if __name__ == "__main__":
    main()
