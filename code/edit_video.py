"""
Extract a portion of a video between start and end times and save to a new file.
Subtitles in the segment are burned in by default (from --subs WebVTT file).
Optional speedup: --speed 2 plays the segment twice as fast.
Usage: python edit_video.py <input_video> <start_time> <end_time> [output_video] [--speed 1] [--subs FILE]
Times can be in MM:SS or HH:MM:SS format (e.g., 31:00, 32:52 or 1:31:00).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_vtt_time(s: str) -> float:
    """Parse WebVTT timestamp HH:MM:SS.mmm or HH:MM:SS to seconds."""
    s = s.strip()
    m = re.match(r"(\d+):(\d+):(\d+)\.?(\d*)", s)
    if not m:
        raise ValueError(f"Invalid VTT time: {s}")
    h, mmm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
    frac = m.group(4)
    sec = h * 3600 + mmm * 60 + ss
    if frac:
        sec += int(frac.ljust(3, "0")[:3]) / 1000.0
    return sec


def format_vtt_time(sec: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{int(s):02d}.{int((s % 1) * 1000):03d}"


def parse_webvtt(path: str) -> list[tuple[float, float, str]]:
    """Parse WebVTT file; return list of (start_sec, end_sec, text)."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\n\n+", content)
    for block in blocks:
        block = block.strip()
        if not block or block.upper() == "WEBVTT":
            continue
        lines = block.split("\n")
        i = 0
        if lines and re.match(r"^\d+$", lines[0].strip()):
            i = 1  # skip cue id
        if i >= len(lines):
            continue
        m = re.match(r"(\d+:\d+:\d+\.?\d*)\s*-->\s*(\d+:\d+:\d+\.?\d*)", lines[i])
        if not m:
            continue
        try:
            start_sec = parse_vtt_time(m.group(1))
            end_sec = parse_vtt_time(m.group(2))
            text = "\n".join(lines[i + 1 :]).strip()
            if text:
                cues.append((start_sec, end_sec, text))
        except (ValueError, IndexError):
            continue
    return cues


def filter_subtitles_for_segment(
    cues: list[tuple[float, float, str]],
    start_sec: float,
    end_sec: float,
    duration_sec: float,
    speed: float = 1.0,
) -> list[tuple[float, float, str]]:
    """Return cues overlapping [start_sec, end_sec], times shifted and scaled for output."""
    out: list[tuple[float, float, str]] = []
    for cstart, cend, text in cues:
        if cend <= start_sec or cstart >= end_sec:
            continue
        new_start = max(0.0, cstart - start_sec) / speed
        new_end = min(duration_sec, cend - start_sec) / speed
        if new_end > new_start:
            out.append((new_start, new_end, text))
    return out


def write_webvtt(cues: list[tuple[float, float, str]], path: str) -> None:
    """Write WebVTT file."""
    lines = ["WEBVTT", ""]
    for i, (start, end, text) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{format_vtt_time(start)} --> {format_vtt_time(end)}")
        lines.append(text)
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def parse_time(s: str) -> float:
    """Parse time string MM:SS or HH:MM:SS to total seconds."""
    s = s.strip()
    # HH:MM:SS or MM:SS
    parts = [int(x) for x in s.split(":")]
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    raise ValueError(f"Invalid time format: {s}. Use MM:SS or HH:MM:SS.")


def format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for ffmpeg."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m}:{s:06.3f}"


def _atempo_chain(speed: float) -> str:
    """Build atempo filter chain (each atempo in [0.5, 2.0])."""
    factors: list[float] = []
    s = speed
    while s > 2.0:
        factors.append(2.0)
        s /= 2.0
    while s < 0.5:
        factors.append(0.5)
        s /= 0.5
    factors.append(s)
    return ",".join(f"atempo={f}" for f in factors)


def extract_segment(
    input_path: str,
    start_time: str,
    end_time: str,
    output_path: str,
    speed: float = 1.0,
    subs_path: str | None = None,
) -> None:
    start_sec = parse_time(start_time)
    end_sec = parse_time(end_time)
    if start_sec >= end_sec:
        raise ValueError("Start time must be before end time.")
    if speed <= 0:
        raise ValueError("Speed must be positive.")
    duration_sec = end_sec - start_sec
    start_str = format_duration(start_sec)
    duration_str = format_duration(duration_sec)

    subs_filter = None
    temp_vtt = None
    if subs_path and Path(subs_path).exists():
        cues = parse_webvtt(subs_path)
        filtered = filter_subtitles_for_segment(cues, start_sec, end_sec, duration_sec, speed)
        if filtered:
            fd, temp_vtt = tempfile.mkstemp(suffix=".vtt")
            try:
                os.close(fd)
                write_webvtt(filtered, temp_vtt)
                # Escape path for ffmpeg subtitles filter (backslash, colon)
                sub_esc = temp_vtt.replace("\\", "\\\\").replace(":", "\\:")
                subs_filter = f"subtitles='{sub_esc}'"
            except Exception:
                if temp_vtt and Path(temp_vtt).exists():
                    Path(temp_vtt).unlink(missing_ok=True)
                temp_vtt = None

    try:
        if speed == 1.0 and not subs_filter:
            cmd = [
                "ffmpeg", "-y", "-ss", start_str, "-i", input_path,
                "-t", duration_str, "-c", "copy", "-avoid_negative_ts", "1",
                output_path,
            ]
        elif speed == 1.0 and subs_filter:
            cmd = [
                "ffmpeg", "-y", "-ss", start_str, "-i", input_path,
                "-t", duration_str,
                "-vf", subs_filter,
                "-c:v", "libx264", "-c:a", "copy", "-avoid_negative_ts", "1",
                output_path,
            ]
        else:
            v_filter = f"setpts=PTS/{speed}"
            if subs_filter:
                v_filter = f"{v_filter},{subs_filter}"
            a_filter = _atempo_chain(speed)
            filter_complex = f"[0:v]{v_filter}[v];[0:a]{a_filter}[a]"
            cmd = [
                "ffmpeg", "-y", "-ss", start_str, "-i", input_path,
                "-t", duration_str,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-c:a", "aac", "-shortest",
                output_path,
            ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"ffmpeg failed with code {result.returncode}")
    finally:
        if temp_vtt and Path(temp_vtt).exists():
            Path(temp_vtt).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract a portion of a video (start–end) and save to a new file."
    )
    parser.add_argument(
        "input_video",
        help="Path to input video (e.g., plasticity_lecture.mp4)",
    )
    parser.add_argument(
        "start_time",
        help="Start time, e.g. 31:00 (MM:SS) or 1:31:00 (HH:MM:SS)",
    )
    parser.add_argument(
        "end_time",
        help="End time, e.g. 32:52 (MM:SS) or 1:32:52 (HH:MM:SS)",
    )
    parser.add_argument(
        "output_video",
        nargs="?",
        default=None,
        help="Output video path. Default: <input>_clip_<start>_<end>.mp4",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        metavar="N",
        help="Playback speed (1=normal, 2=twice as fast). Requires audio when not 1. Default: 1",
    )
    default_subs = "output_explore/plasticity_lecture_subtitles.txt"
    parser.add_argument(
        "--subs",
        default=default_subs,
        metavar="FILE",
        help=f"WebVTT subtitle file. Cues in the extracted time range are burned in. Default: {default_subs}",
    )
    parser.add_argument(
        "--no-subs",
        action="store_true",
        help="Do not burn subtitles (overrides --subs).",
    )
    args = parser.parse_args()

    input_path = Path(args.input_video)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.output_video:
        output_path = args.output_video
    else:
        # Sanitize time for filename: 31:00 -> 31-00
        start_safe = re.sub(r"[:\s]+", "-", args.start_time)
        end_safe = re.sub(r"[:\s]+", "-", args.end_time)
        stem = input_path.stem
        suffix = input_path.suffix or ".mp4"
        output_path = str(input_path.parent / f"{stem}_clip_{start_safe}_to_{end_safe}{suffix}")

    subs_path = None if args.no_subs else (args.subs if Path(args.subs).exists() else None)
    try:
        extract_segment(
            args.input_video,
            args.start_time,
            args.end_time,
            output_path,
            speed=args.speed,
            subs_path=subs_path,
        )
        print(f"Saved segment to: {output_path}")
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()