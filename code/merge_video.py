"""
Merge a series of videos in order into a single output video.
Usage: python merge_video.py -o output.mp4 video1.mp4 video2.mp4 video3.mp4

Uses the concat filter (decode → concat → encode) so audio and video are
one continuous stream with no timestamp discontinuities at boundaries.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Target audio sample rate so all segments match for concat
AUDIO_SAMPLE_RATE = 44100


def merge_videos(input_paths: list[str], output_path: str) -> None:
    """Merge videos in order using ffmpeg concat filter (decode → concat → encode)."""
    for p in input_paths:
        if not Path(p).exists():
            raise FileNotFoundError(f"Input file not found: {p}")

    n = len(input_paths)
    # Build: -i v0 -i v1 ... and filter [0:v][0:a][1:v][1:a]... concat=n=N:v=1:a=1[v][a]
    # Normalize audio to same sample rate so concat doesn't glitch at boundaries
    parts = []
    for i in range(n):
        # aresample to fixed rate; async=1 helps keep A/V in sync across boundaries
        parts.append(f"[{i}:a]aresample={AUDIO_SAMPLE_RATE}:async=1[a{i}]")
    concat_inputs = "".join(f"[{i}:v][a{i}]" for i in range(n))
    filter_complex = ";".join(parts) + ";" + concat_inputs + f"concat=n={n}:v=1:a=1[v][a]"

    cmd = [
        "ffmpeg", "-y",
        *sum([["-i", str(Path(p).resolve())] for p in input_paths], []),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed with code {result.returncode}")


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
