#!/usr/bin/env python3
from __future__ import annotations

"""
Overlay an image on a video at a specified position and size.
Position and size are given as ratios (0–1) of the video width/height.
Usage: python insert_image.py -i video.mp4 -img logo.png -o out.mp4
       python insert_image.py -i video.mp4 -o out.mp4 --config image_vid_2.txt
Default: bottom-left, size 1/4 width x 1/5 height; image covers that region for the whole video.
With --config or start/end: overlay only between start and end time (seconds).
"""

import argparse
import json
import re
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


def get_video_duration(video_path: str) -> float | None:
    """Return duration in seconds of the first video stream, or None if unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration",
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
        d = streams[0].get("duration")
        if d is None:
            # format-level duration fallback
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            d = data.get("format", {}).get("duration")
        if d is None:
            return None
        return float(d)
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


def parse_size(s: str) -> tuple[float, ...]:
    """Parse size as one number (scale, keep aspect) or two (width_ratio, height_ratio)."""
    s = s.strip()
    if "," in s:
        return parse_ratio_pair(s, "size")
    try:
        v = float(s)
        if not (0 < v <= 1):
            raise ValueError("size must be in (0, 1]")
        return (v,)
    except ValueError:
        raise ValueError("size must be one number (e.g. 0.1) or two (e.g. 0.1,0.2)")


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


def parse_time_to_sec(s: str) -> float:
    """Parse MM:SS or HH:MM:SS or plain seconds to float."""
    s = s.strip()
    if re.match(r"^\d+\.?\d*$", s):
        return float(s)
    parts = [float(x) for x in re.findall(r"[\d.]+", s)]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Invalid time: {s}. Use MM:SS or HH:MM:SS or seconds.")


def parse_align_ratio(s: str, axis: str) -> float:
    """Parse x or y: left/top=0, center=0.5, right/bottom=1, or a number 0–1."""
    s = s.strip().lower()
    if s in ("left", "top"):
        return 0.0
    if s == "center":
        return 0.5
    if s in ("right", "bottom"):
        return 1.0
    try:
        v = float(s)
        if 0 <= v <= 1:
            return v
    except ValueError:
        pass
    raise ValueError(f"{axis} must be left|center|right, top|center|bottom, or 0–1")


def parse_image_config(config_path: str, base_dir: str | None = None) -> dict:
    """
    Parse an image overlay config file. Returns dict with:
    image (path), start_sec, end_sec, position (x,y), size (w,h).
    Paths in the file are relative to the config file's directory unless base_dir is set.
    Format (one overlay per file):
      image: path/to/image.png
      start: 00:00:20
      end: 00:00:35
      x: right
      y: top
      size: 0.1   (one number = scale, keep aspect) or 0.1,0.2 (width, height ratios)
    Optional: segment_start and speed. When both are set, start/end are treated as
    source-lecture timestamps and converted to clip time: clip_t = (lecture_t - segment_start) / speed.
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(config_path)
    base = Path(base_dir) if base_dir else path.parent
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    out: dict = {}
    for line in lines:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "image":
            out["image"] = str((base / val).resolve()) if not Path(val).is_absolute() else val
        elif key == "start":
            out["start_sec"] = parse_time_to_sec(val)
        elif key == "end":
            out["end_sec"] = parse_time_to_sec(val)
        elif key == "time" and "--" in val:
            start_s, _, end_s = val.partition("--")
            out["start_sec"] = parse_time_to_sec(start_s.strip())
            out["end_sec"] = parse_time_to_sec(end_s.strip())
        elif key == "segment_start":
            out["segment_start_sec"] = parse_time_to_sec(val)
        elif key == "speed":
            out["speed"] = float(val.strip())
        elif key == "x":
            out["x"] = parse_align_ratio(val, "x")
        elif key == "y":
            out["y"] = parse_align_ratio(val, "y")
        elif key == "size":
            out["size"] = parse_size(val)
    if "image" not in out:
        raise ValueError("Config must contain 'image: path'")
    out.setdefault("start_sec", None)
    out.setdefault("end_sec", None)
    # Convert lecture time to clip time when segment_start and speed are present
    seg = out.get("segment_start_sec")
    sp = out.get("speed")
    if seg is not None and sp is not None and sp > 0:
        if out.get("start_sec") is not None:
            out["start_sec"] = max(0.0, (out["start_sec"] - seg) / sp)
        if out.get("end_sec") is not None:
            out["end_sec"] = max(0.0, (out["end_sec"] - seg) / sp)
    out.setdefault("x", 0.0)
    out.setdefault("y", 0.9)
    out.setdefault("size", (0.25, 0.2))
    out["position"] = (out["x"], out["y"])
    return out


def insert_image(
    video_path: str,
    output_path: str,
    position: tuple[float, float],
    size: tuple[float, float],
    fill_box: bool = False,
    image_path: str | None = None,
    test_mode: bool = False,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> None:
    """Overlay image on video. position = top-left (ratios). size = (wr, hr) or (scale,) to keep aspect. If start_sec/end_sec set, overlay only in that time range."""
    xr, yr = position
    v_size = get_video_size(video_path)
    # Resolve single-number size (scale) to (wr, hr) preserving image aspect ratio
    if len(size) == 1:
        scale = size[0]
        img_size = get_image_size(image_path) if image_path else None
        if v_size and img_size:
            vw, vh = v_size
            iw, ih = img_size
            rw, rh = iw / vw, ih / vh
            m = max(rw, rh)
            wr = scale * rw / m
            hr = scale * rh / m
        else:
            wr = hr = scale
        size = (wr, hr)
    wr, hr = size
    enable_expr = None
    if start_sec is not None and end_sec is not None:
        enable_expr = f"between(t,{start_sec},{end_sec})"
    _en = ":enable='{}'".format(enable_expr) if enable_expr else ""
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
        # x: left=0, center=0.5 (center box), right=1 (right-edge of box at video right)
        if xr <= 0.01:
            x = 0
        elif xr >= 0.99:
            x = vw - box_w
        elif 0.49 <= xr <= 0.51:
            x = (vw - box_w) // 2
        else:
            x = int(vw * xr)
        # y: top=0, center=0.5, bottom=1
        if yr <= 0.01:
            y = 0
        elif yr >= 0.99:
            y = vh - box_h
        elif 0.49 <= yr <= 0.51:
            y = (vh - box_h) // 2
        else:
            y = min(int(vh * yr), vh - box_h - 1)
        x, y = max(0, x), max(0, y)
        if fill_box:
            scale_filter = "[1:v]scale={}:{}[s];[s]format=yuv420p[sy]".format(box_w, box_h)
        else:
            scale_filter = "[1:v]scale={}:{}:force_original_aspect_ratio=decrease[s];[s]format=yuv420p[sy]".format(box_w, box_h)
        # Drawbox identical to test (c=white:t=fill), then overlay image at same (x,y)
        filter_parts = [
            "[0:v]drawbox=x={}:y={}:w={}:h={}:c=white:t=fill{}[vbg]".format(x, y, box_w, box_h, _en),
            scale_filter,
            "[vbg][sy]overlay=x={}:y={}{}[outv]".format(x, y, _en),
            "[outv]scale=trunc(iw/2)*2:trunc(ih/2)*2[outv2]",
            "[outv2]format=yuv420p[outv3]",
        ]
        map_label = "[outv3]"
    else:
        # Expression path: position and size as expressions; respect xr/yr (left/right, top/bottom)
        def f(x: float) -> str:
            return format(x, ".6f").rstrip("0").rstrip(".")
        wrs, hrs = f(wr), f(hr)
        if hr >= 0.99:
            scale_filter = "[1:v][0:v]scale=w=-1:h='rh'[s];[s]format=yuv420p[sy]"
            box_w_expr = "iw"
        elif fill_box:
            scale_filter = "[1:v][0:v]scale=w='rw*{}':h='rh*{}'[s];[s]format=yuv420p[sy]".format(wrs, hrs)
            box_w_expr = "iw*" + wrs
        else:
            scale_filter = "[1:v][0:v]scale=w='rw*{}':h='rh*{}':force_original_aspect_ratio=decrease[s];[s]format=yuv420p[sy]".format(wrs, hrs)
            box_w_expr = "iw*" + wrs
        # Box and overlay position: left=0, right=iw-(box), center=(iw-(box))/2; same for y
        if xr <= 0.01:
            box_x_expr = "0"
            overlay_x_expr = "0"
        elif xr >= 0.99:
            box_x_expr = "iw-iw*" + wrs if not (hr >= 0.99) else "0"
            overlay_x_expr = "main_w-overlay_w"
        elif 0.49 <= xr <= 0.51:
            box_x_expr = "(iw-iw*" + wrs + ")/2" if not (hr >= 0.99) else "(iw-iw)/2"
            overlay_x_expr = "(main_w-overlay_w)/2"
        else:
            box_x_expr = "iw*" + f(xr)
            overlay_x_expr = "main_w*" + f(xr)
        if yr <= 0.01:
            box_y_expr = "0"
            overlay_y_expr = "0"
        elif yr >= 0.99:
            box_y_expr = "ih-ih*" + hrs
            overlay_y_expr = "main_h-overlay_h"
        elif 0.49 <= yr <= 0.51:
            box_y_expr = "(ih-ih*" + hrs + ")/2"
            overlay_y_expr = "(main_h-overlay_h)/2"
        else:
            box_y_expr = "ih*" + f(yr)
            overlay_y_expr = "main_h*" + f(yr)
        filter_parts = [
            "[0:v]drawbox=x='{}':y='{}':w='{}':h='ih*{}':c=white:t=fill{}[vbg]".format(box_x_expr, box_y_expr, box_w_expr, hrs, _en),
            scale_filter,
            "[vbg][sy]overlay=x='{}':y='{}'{}[outv]".format(overlay_x_expr, overlay_y_expr, _en),
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
        default="0.25,0.2",
        metavar="SIZE",
        help="One number (e.g. 0.1) = scale to that fraction of frame, keep image aspect; or width,height (e.g. 0.1,0.2).",
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
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Read image path, start/end time, position (x,y), size from config file. Overrides -img, --position, --size, --start, --end.",
    )
    parser.add_argument(
        "--start",
        metavar="SEC",
        help="Start time for overlay (seconds or MM:SS). With --end, overlay only in this range.",
    )
    parser.add_argument(
        "--end",
        metavar="SEC",
        help="End time for overlay (seconds or MM:SS). With --start, overlay only in this range.",
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: video not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    start_sec = end_sec = None
    if args.config:
        try:
            cfg = parse_image_config(args.config)
            image_path = cfg["image"]
            position = cfg["position"]
            size = cfg["size"]
            start_sec = cfg.get("start_sec")
            end_sec = cfg.get("end_sec")
            args.image = image_path
            # Clamp start/end to video duration so overlay actually appears
            if start_sec is not None or end_sec is not None:
                dur = get_video_duration(args.input)
                if dur is not None and dur > 0:
                    if start_sec is not None:
                        start_sec = max(0.0, min(start_sec, dur))
                    if end_sec is not None:
                        end_sec = max(0.0, min(end_sec, dur))
                    if start_sec is not None and end_sec is not None and start_sec >= end_sec:
                        start_sec = end_sec = None  # invalid window -> show for whole video
        except (ValueError, FileNotFoundError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            position = parse_ratio_pair(args.position, "position")
            size = parse_size(args.size)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if args.start is not None and args.end is not None:
            start_sec = parse_time_to_sec(args.start)
            end_sec = parse_time_to_sec(args.end)

    if not args.test and not Path(args.image).exists():
        print(f"Error: image not found: {args.image}", file=sys.stderr)
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
            start_sec=start_sec,
            end_sec=end_sec,
        )
        print(f"Output written to: {args.output}")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
