"""
Extract a portion of a video between start and end times and save to a new file.
Subtitles in the segment are burned in by default (from --subs WebVTT file).
Optional speedup: --speed 2 plays the segment twice as fast.
Order when speed != 1: first extract the segment at 1x from the raw video, then apply speedup (and subs) to that clip.
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


def parse_plain_text_subs(path: str, output_duration_sec: float) -> list[tuple[float, float, str]]:
    """Parse plain-text file (one line per cue); assign times uniformly from 0 to output_duration_sec with no gap at the end."""
    lines = [s.strip() for s in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if s.strip()]
    if not lines:
        return []
    n = len(lines)
    if n == 1:
        return [(0.0, output_duration_sec, lines[0])]
    d = output_duration_sec / n
    out: list[tuple[float, float, str]] = []
    for i, text in enumerate(lines):
        start = i * d
        # Last cue ends exactly at output_duration_sec so no point in the video has no subtitle.
        end = output_duration_sec if i == n - 1 else (i + 1) * d
        out.append((start, end, text))
    return out


def plain_text_with_webvtt_timing(
    plain_path: str,
    timing_path: str,
    start_sec: float,
    end_sec: float,
    duration_sec: float,
    speed: float,
) -> list[tuple[float, float, str]]:
    """Use WebVTT at timing_path for cue times, plain-text lines at plain_path for text. 1:1 by order: line 1 = first cue, line 2 = second cue, etc."""
    timing_cues = parse_webvtt(timing_path)
    if not timing_cues:
        return []
    filtered = filter_subtitles_for_segment(timing_cues, start_sec, end_sec, duration_sec, speed)
    plain_lines = [s.strip() for s in Path(plain_path).read_text(encoding="utf-8", errors="replace").splitlines() if s.strip()]
    if not plain_lines:
        return filtered
    out: list[tuple[float, float, str]] = []
    out_dur = duration_sec / speed
    for i, (s, e, orig_text) in enumerate(filtered):
        text = plain_lines[i] if i < len(plain_lines) else orig_text
        out.append((s, e, text))
    if len(plain_lines) > len(filtered):
        last_end = out[-1][1] if out else 0.0
        remaining = len(plain_lines) - len(filtered)
        span = (out_dur - last_end) / remaining if remaining > 0 else 1.0
        for j in range(remaining):
            start = last_end + j * span
            end = start + span
            out.append((start, end, plain_lines[len(filtered) + j]))
    return out


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


def _normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, remove punctuation for matching."""
    s = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(s.split())


def _phrase_word_match_score(phrase_norm: str, cue_text: str) -> float:
    """
    Score how well phrase matches cue: 0..1.
    Uses in-order word presence; all phrase words in cue in order gives 1.
    """
    pw = phrase_norm.split()
    cw = _normalize_text(cue_text).split()
    if not pw:
        return 1.0
    j = 0
    for i, w in enumerate(cw):
        if j < len(pw) and pw[j] == w:
            j += 1
        elif j < len(pw) and pw[j] in w:
            j += 1
    return j / len(pw) if pw else 0.0


def _find_best_matching_cue(
    cues: list[tuple[float, float, str]], phrase: str
) -> tuple[int, float] | None:
    """
    Find the cue that best matches the user phrase (may not be exact).
    Returns (index, start_sec) of best cue, or None if no usable match.
    """
    phrase_norm = _normalize_text(phrase)
    if not phrase_norm:
        return None
    best: tuple[int, float, float] | None = None  # (index, start_sec, score)
    for i, (start, end, text) in enumerate(cues):
        cue_norm = _normalize_text(text)
        if phrase_norm in cue_norm:
            # Exact substring: prefer earlier in segment
            score = 2.0 - (start / 3600.0) * 0.001  # tie-break by start
            if best is None or score > best[2]:
                best = (i, start, score)
        else:
            # Fuzzy: phrase words in order in cue
            score = _phrase_word_match_score(phrase_norm, text)
            if score >= 0.5:
                if best is None or score > best[2]:
                    best = (i, start, score)
                elif score == best[2] and start < best[1]:
                    best = (i, start, score)
    if best is None:
        return None
    return (best[0], best[1])


def apply_word_mark_offset(
    cues: list[tuple[float, float, str]], phrase: str
) -> list[tuple[float, float, str]]:
    """
    Shift cue times so the cue that best matches the phrase starts at 0.
    Drops cues that would end before 0; clamps start to 0 for cues that overlap.
    """
    match = _find_best_matching_cue(cues, phrase)
    if match is None:
        return cues
    _idx, anchor_start = match
    offset = anchor_start
    out: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        new_start = start - offset
        new_end = end - offset
        if new_end <= 0:
            continue
        out.append((max(0.0, new_start), new_end, text))
    return out


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
    subs_timing_path: str | None = None,
    word_mark: str | None = None,
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
    used_plain_text_subs = False
    if subs_path and Path(subs_path).exists():
        cues = parse_webvtt(subs_path)
        if cues:
            filtered = filter_subtitles_for_segment(cues, start_sec, end_sec, duration_sec, speed)
        elif subs_timing_path and Path(subs_timing_path).exists():
            filtered = plain_text_with_webvtt_timing(
                subs_path, subs_timing_path, start_sec, end_sec, duration_sec, speed
            )
            used_plain_text_subs = True
        else:
            # Plain-text only (e.g. interm vid_N.txt): one line per cue, even spacing over output duration.
            out_dur = duration_sec / speed
            filtered = parse_plain_text_subs(subs_path, out_dur)
            used_plain_text_subs = True
        # Don't apply word_mark when using plain-text subs; line order is canonical.
        if filtered and word_mark and word_mark.strip() and not used_plain_text_subs:
            filtered = apply_word_mark_offset(filtered, word_mark.strip())
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

    # Use -ss after -i for accurate seeking (avoids A/V desync from keyframe-only seek).
    try:
        if speed == 1.0 and not subs_filter:
            cmd = [
                "ffmpeg", "-y", "-i", input_path, "-ss", start_str, "-t", duration_str,
                "-c", "copy", "-avoid_negative_ts", "1",
                output_path,
            ]
        elif speed == 1.0 and subs_filter:
            # Reset PTS to 0 so subtitle times (0 to duration) match the video timeline.
            v_filter = f"setpts=PTS-STARTPTS,{subs_filter}"
            cmd = [
                "ffmpeg", "-y", "-i", input_path, "-ss", start_str, "-t", duration_str,
                "-vf", v_filter,
                "-c:v", "libx264", "-c:a", "copy", "-avoid_negative_ts", "1",
                output_path,
            ]
        else:
            # Order: first extract segment at 1x, then apply speedup (and subs) to the extracted clip.
            temp_extract = None
            try:
                fd, temp_extract = tempfile.mkstemp(suffix=".mp4", prefix="edit_video_extract_")
                os.close(fd)
                # Pass 1: extract segment at 1x (no speed, no subs).
                cmd1 = [
                    "ffmpeg", "-y", "-i", input_path, "-ss", start_str, "-t", duration_str,
                    "-c:v", "libx264", "-c:a", "aac", "-avoid_negative_ts", "1",
                    temp_extract,
                ]
                result = subprocess.run(cmd1, capture_output=True, text=True)
                if result.returncode != 0:
                    print(result.stderr, file=sys.stderr)
                    raise RuntimeError(f"ffmpeg extract failed with code {result.returncode}")
                # Pass 2: speed up (and burn subs) the extracted clip. Subtitle times are 0 to duration_sec/speed.
                v_filter = f"setpts=PTS/{speed},setpts=PTS-STARTPTS"
                if subs_filter:
                    v_filter = f"{v_filter},{subs_filter}"
                a_filter = _atempo_chain(speed)
                filter_complex = f"[0:v]{v_filter}[v];[0:a]{a_filter}[a]"
                out_duration = duration_sec / speed
                out_duration_str = format_duration(out_duration)
                cmd2 = [
                    "ffmpeg", "-y", "-i", temp_extract,
                    "-filter_complex", filter_complex,
                    "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-c:a", "aac",
                    "-t", out_duration_str,
                    "-avoid_negative_ts", "1",
                    output_path,
                ]
                result = subprocess.run(cmd2, capture_output=True, text=True)
                if result.returncode != 0:
                    print(result.stderr, file=sys.stderr)
                    raise RuntimeError(f"ffmpeg speedup failed with code {result.returncode}")
            finally:
                if temp_extract and Path(temp_extract).exists():
                    Path(temp_extract).unlink(missing_ok=True)
        if speed == 1.0:
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
    parser.add_argument(
        "--subs-timing",
        default=None,
        metavar="FILE",
        help="WebVTT file to use for cue timing when --subs is plain text (one line per cue). Omit to use even spacing.",
    )
    parser.add_argument(
        "--word-mark",
        default=None,
        metavar="TEXT",
        help="First few words at start of this clip; subtitle times are shifted so the best-matching cue starts at 0 (use with word_mark.txt).",
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
            subs_timing_path=args.subs_timing,
            word_mark=args.word_mark,
        )
        print(f"Saved segment to: {output_path}")
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()