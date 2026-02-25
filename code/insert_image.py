#!/usr/bin/env python3
from __future__ import annotations

"""
Overlay an image on a video at a specified position and size.
Position and size are given as ratios (0–1) of the video width/height.
Usage: python insert_image.py -i video.mp4 -img logo.png -o out.mp4
Default: bottom-left, size 1/4 width x 1/5 height; image covers that region for the whole video.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def get_video_size(video_path: str) -> tuple[int, int] | None:
    """Return (width, height) of the first video stream, or None if ffprobe not available."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        w, h = int(s["width"]), int(s["height"])
        return (w, h) if w > 0 and h > 0 else None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def get_image_size(image_path: str) -> tuple[int, int] | None:
    """Return (width, height) of the image, or None if ffprobe not available."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                image_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        w, h = int(s["width"]), int(s["height"])
        return (w, h) if w > 0 and h > 0 else None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def parse_ratio_pair(s: str, name: str) -> tuple[float, float]:
    """Parse 'x,y' into two floats in [0, 1] (or 0–100 interpreted as percent)."""
    parts = s.split(",")
    if len(parts) != 2:
        raise ValueError(f"{name} must be two numbers separated by a comma (e.g., 0.05,0.05)")
    try:
        a, b = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        raise ValueError(f"{name} must be numeric (e.g., 0.05,0.05 or 5,5)")
    # Allow 0–100 as percentage
    if 0 < a <= 100 and 0 < b <= 100:
        a, b = a / 100.0, b / 100.0
    if not (0 <= a <= 1 and 0 <= b <= 1):
        raise ValueError(f"{name} values must be between 0 and 1 (or 0–100 as percent)")
    return a, b


def insert_image(
    video_path: str,
    output_path: str,
    position: tuple[float, float],
    size: tuple[float, float],
    fill_box: bool = False,
    image_path: str | None = None,
    test_mode: bool = False,
) -> None:
    """Overlay image on video (or --test: just draw a white block). position = top-left (ratios). size = box (ratios)."""
    xr, yr = position
    wr, hr = size
    # Prefer exact pixels when ffprobe returns sane dimensions (both >= 320 to avoid thumbnail streams)
    v_size = get_video_size(video_path)
    use_pixels = v_size is not None and v_size[0] >= 320 and v_size[1] >= 320

    if test_mode:
        # Only draw a white block (no image). Half of default size (0.25,0.2) -> (0.125, 0.1).
        # Bottom-left: corners match frame — left x=0, bottom of box at bottom of frame so y = vh - box_h.
        test_wr, test_hr = 0.5, 0.3
        if use_pixels:
            vw, vh = v_size
            box_w = max(2, int(vw * test_wr) // 2 * 2)
            box_h = max(2, int(vh * test_hr) // 2 * 2)
            x = 0
            y = vh - box_h  # bottom of box flush with bottom of frame
            filter_complex = (
                "[0:v]drawbox=x={}:y={}:w={}:h={}:c=white:t=fill[outv];"
                "[outv]scale=trunc(iw/2)*2:trunc(ih/2)*2[outv2];"
                "[outv2]format=yuv420p[outv3]"
            ).format(x, y, box_w, box_h)
        else:
            # Expression path: no ffprobe. x=0, y=ih-h (bottom flush), w=iw*0.125, h=ih*0.1
            def f(x: float) -> str:
                return format(x, ".6f").rstrip("0").rstrip(".")
            wrs, hrs = f(test_wr), f(test_hr)
            filter_complex = (
                "[0:v]drawbox=x=0:y='ih-ih*{}':w='iw*{}':h='ih*{}':c=white:t=fill[outv];"
                "[outv]scale=trunc(iw/2)*2:trunc(ih/2)*2[outv2];"
                "[outv2]format=yuv420p[outv3]"
            ).format(hrs, wrs, hrs)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv3]", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "copy",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"ffmpeg failed with code {result.returncode}")
        return

    # Normal path: same as test (drawbox with c=white, bottom-left flush) then overlay image on top
    if use_pixels:
        vw, vh = v_size
        box_h = max(2, int(vh * hr) // 2 * 2)
        # When full height (hr~1), compute box_w from image aspect to keep image ratio
        if hr >= 0.99:
            img_size = get_image_size(image_path)
            if img_size is not None:
                iw, ih = img_size
                box_w = min(vw, max(2, int(vh * iw / ih) // 2 * 2))
            else:
                box_w = max(2, int(vw * wr) // 2 * 2)
        else:
            box_w = max(2, int(vw * wr) // 2 * 2)
        # Match test: bottom-left flush — x=0, y=vh-box_h
        x = 0 if xr <= 0.01 else int(vw * xr)
        y = (vh - box_h) if (yr >= 0.89 and xr <= 0.01) else min(int(vh * yr), vh - box_h - 1)
        x, y = max(0, x), max(0, y)
        if fill_box:
            scale_filter = "[1:v]scale={}:{}[s];[s]format=yuv420p[sy]".format(box_w, box_h)
        else:
            scale_filter = "[1:v]scale={}:{}:force_original_aspect_ratio=decrease[s];[s]format=yuv420p[sy]".format(box_w, box_h)
        # Drawbox identical to test (c=white:t=fill), then overlay image at same (x,y)
        filter_parts = [
            "[0:v]drawbox=x={}:y={}:w={}:h={}:c=white:t=fill[vbg]".format(x, y, box_w, box_h),
            scale_filter,
            "[vbg][sy]overlay=x={}:y={}[outv]".format(x, y),
            "[outv]scale=trunc(iw/2)*2:trunc(ih/2)*2[outv2]",
            "[outv2]format=yuv420p[outv3]",
        ]
        map_label = "[outv3]"
    else:
        # Expression path: drawbox same as test; scale image by ref; overlay at bottom flush
        def f(x: float) -> str:
            return format(x, ".6f").rstrip("0").rstrip(".")
        wrs, hrs = f(wr), f(hr)
        if hr >= 0.99:
            # Full height: scale image to ref height, width auto (keeps image ratio)
            scale_filter = "[1:v][0:v]scale=w=-1:h='rh'[s];[s]format=yuv420p[sy]"
        elif fill_box:
            scale_filter = "[1:v][0:v]scale=w='rw*{}':h='rh*{}'[s];[s]format=yuv420p[sy]".format(wrs, hrs)
        else:
            scale_filter = "[1:v][0:v]scale=w='rw*{}':h='rh*{}':force_original_aspect_ratio=decrease[s];[s]format=yuv420p[sy]".format(wrs, hrs)
        overlay_y = "main_h-main_h*" + hrs if (yr >= 0.89 and xr <= 0.01) else "main_h*" + f(yr)
        box_w_expr = "iw" if hr >= 0.99 else "iw*" + wrs
        filter_parts = [
            "[0:v]drawbox=x=0:y='ih-ih*{}':w='{}':h='ih*{}':c=white:t=fill[vbg]".format(hrs, box_w_expr, hrs),
            scale_filter,
            "[vbg][sy]overlay=x=0:y='{}'[outv]".format(overlay_y),
            "[outv]scale=trunc(iw/2)*2:trunc(ih/2)*2[outv2]",
            "[outv2]format=yuv420p[outv3]",
        ]
        map_label = "[outv3]"
    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-loop", "1",
        "-i", image_path,
        "-filter_complex", filter_complex,
        "-map", map_label,
        "-map", "0:a?",
        "-c:v", "libx264",
        "-c:a", "copy",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"ffmpeg failed with code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Insert an image overlay on a video at a given position and size (ratios).",
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        metavar="VIDEO",
        help="Input video path",
    )
    parser.add_argument(
        "-img", "--image",
        default="output_explore/vM_yield_criteria.png",
        metavar="IMAGE",
        help="Image to overlay. Default: output_explore/vM_yield_criteria.png. Not needed with --test.",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output video path",
    )
    parser.add_argument(
        "--position",
        default="0,0.9",
        metavar="X,Y",
        help="Top-left corner of overlay (ratio of video). Default: bottom-left flush (0, 0.9).",
    )
    parser.add_argument(
        "--size",
        default="30,20",
        metavar="W,H",
        help="Overlay box as ratio of video width,height. Default: full height (20x tall), width from image ratio. Image keeps aspect unless --fill.",
    )
    parser.add_argument(
        "--fill",
        action="store_true",
        help="Stretch image to exactly fill the size box; default is to fit inside (keep aspect ratio).",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: draw only a white block (no image) to verify the pipeline. Same position/size as default.",
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: video not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not args.test and not Path(args.image).exists():
        print(f"Error: image not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    try:
        position = parse_ratio_pair(args.position, "position")
        size = parse_ratio_pair(args.size, "size")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        insert_image(
            args.input,
            args.output,
            position,
            size,
            fill_box=args.fill,
            image_path=args.image,
            test_mode=args.test,
        )
        print(f"Output written to: {args.output}")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
