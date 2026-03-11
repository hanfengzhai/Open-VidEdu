"""
Merge a series of videos in order into a single output video.
Usage: python merge_video.py -o output.mp4 video1.mp4 video2.mp4 video3.mp4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def merge_videos(input_paths: list[str], output_path: str) -> None:
    """Merge videos in order using ffmpeg concat demuxer."""
    for p in input_paths:
        if not Path(p).exists():
            raise FileNotFoundError(f"Input file not found: {p}")

    # Concat list file: each line is "file 'path'"
    # Escape single quotes in path for ffmpeg
    def escape(path: str) -> str:
        return path.replace("'", "'\\''")

    lines = [f"file '{escape(str(Path(p).resolve()))}'" for p in input_paths]
    list_content = "\n".join(lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
    ) as f:
        f.write(list_content)
        list_path = f.name

    try:
        # Re-encode so all segments share same format/timebase and boundaries don't glitch audio.
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"ffmpeg failed with code {result.returncode}")
    finally:
        Path(list_path).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge a series of videos in order into one output video.",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output video path (e.g., merged.mp4)",
    )
    parser.add_argument(
        "videos",
        nargs="+",
        help="Input video files in desired order (e.g., part1.mp4 part2.mp4 part3.mp4)",
    )
    args = parser.parse_args()

    try:
        merge_videos(args.videos, args.output)
        print(f"Merged {len(args.videos)} video(s) into: {args.output}")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
