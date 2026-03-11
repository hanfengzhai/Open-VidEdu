#!/bin/bash
#SBATCH --job-name=video_workflow
#SBATCH --output=bash_output/video_workflow_%j.out
#SBATCH --error=bash_output/video_workflow_%j.err
#SBATCH --time=2-00:00:00
#SBATCH -p cpu
# Generate sub-clips per plan/Outline.md into code/interm_videos, then merge them in order into one video.
# Optional: add lines to code/word_mark.txt like "new_vid_2.mp4: first few words" to align subtitles (closest match).
# When code/interm_videos/subtitle_vid_N.txt exists it is the only subtitle source for new_vid_N.mp4 (one line per cue, even spacing).
# When code/interm_videos/image_vid_N.txt exists, overlay the image on new_vid_N.mp4 per that config (start/end time, x/y, size).
# Run from the directory that contains raw_videos/ and this script:  cd code && sbatch submit_workflow.sh

mkdir -p bash_output
set -e
# Under sbatch the job CWD is often the spool dir; use SLURM_SUBMIT_DIR so we run in the submit dir.
if [ -n "${SLURM_SUBMIT_DIR:-}" ]; then
  CODE_DIR="$SLURM_SUBMIT_DIR"
else
  CODE_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
cd "$CODE_DIR"
RAW="${CODE_DIR}/raw_videos"
INTERM="${CODE_DIR}/interm_videos"
PROCESSED="${CODE_DIR}/processed"
SUBS="${RAW}/plasticity_lecture_subtitles.txt"
WORD_MARK_FILE="${CODE_DIR}/word_mark.txt"

mkdir -p "$INTERM" "$PROCESSED"
echo "Working directory: $CODE_DIR"

# Get optional word-mark phrase for an interm output (e.g. new_vid_2.mp4). Lines: "key: phrase" or "key: \"phrase\""
get_word_mark() {
  local key="$1" line prefix rest
  [ -f "$WORD_MARK_FILE" ] || return
  line=$(grep -F "$key:" "$WORD_MARK_FILE" | grep -v '^[[:space:]]*#' | head -1) || return
  line=$(echo "$line" | sed 's/^[[:space:]]*//')
  prefix="$key: "
  rest="${line#$prefix}"
  rest="${rest#\"}"; rest="${rest%\"}"
  rest="${rest#\'}"; rest="${rest%\'}"
  echo "$rest" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

# Subtitle source for interm video N: use INTERM/subtitle_vid_N.txt if it exists, else main SUBS (WebVTT).
get_subs_for_vid() {
  local n="$1"
  if [ -f "$INTERM/subtitle_vid_${n}.txt" ]; then
    echo "$INTERM/subtitle_vid_${n}.txt"
  else
    echo "$SUBS"
  fi
}

# If INTERM/image_vid_N.txt exists and contains "image:", overlay that image on INTERM/new_vid_N.mp4 (in-place).
# Temp output must have .mp4 extension so ffmpeg picks the mp4 muxer.
# Empty or invalid config is skipped so the workflow does not fail.
apply_image_overlay() {
  local n="$1"
  local img_cfg="$INTERM/image_vid_${n}.txt"
  local vid="$INTERM/new_vid_${n}.mp4"
  local tmp_out="$INTERM/new_vid_${n}_ov.mp4"
  if [ -f "$img_cfg" ] && [ -f "$vid" ] && grep -q 'image:' "$img_cfg"; then
    python "$CODE_DIR/insert_image.py" -i "$vid" -o "$tmp_out" --config "$img_cfg" && mv "$tmp_out" "$vid"
  fi
}
# ---- Sub-clips (Outline sections: ---) ----
# Order: extract segment at 1x from raw, then apply speedup. Final duration = extracted_duration / speed (e.g. 33s @ 1.5x -> 22s).
# what is plasticity
python "$CODE_DIR/edit_video.py" \
  "$RAW/Bending_the_Rules.mp4" 00:00:15 00:00:48 "$INTERM/new_vid_1.mp4" --speed 1.5 --no-subs

WM2=$(get_word_mark "new_vid_2.mp4")
WM2_ARGS=(); [ -n "$WM2" ] && WM2_ARGS=(--word-mark "$WM2")
SUBS2=$(get_subs_for_vid 2)
python "$CODE_DIR/edit_video.py" \
  "$RAW/plasticity_lecture.mp4" 00:00:14 00:01:15 "$INTERM/new_vid_2.mp4" --speed 1.5 --subs "$SUBS2" "${WM2_ARGS[@]}"
apply_image_overlay 2

WM4=$(get_word_mark "new_vid_4.mp4")
python "$CODE_DIR/edit_video.py" \
  "$RAW/Bending_the_Rules.mp4" 00:01:06 00:01:50 "$INTERM/new_vid_3.mp4" --speed 1.5 --no-subs
WM4_ARGS=(); [ -n "$WM4" ] && WM4_ARGS=(--word-mark "$WM4")
SUBS4=$(get_subs_for_vid 4)
python "$CODE_DIR/edit_video.py" \
  "$RAW/plasticity_lecture.mp4" 00:01:15 00:07:42 "$INTERM/new_vid_4.mp4" --speed 1.5 --subs "$SUBS4" "${WM4_ARGS[@]}"
apply_image_overlay 4

# WM6=$(get_word_mark "new_vid_5.mp4")
# WM6_ARGS=(); [ -n "$WM6" ] && WM6_ARGS=(--word-mark "$WM6")
# SUBS6=$(get_subs_for_vid 5)
# python "$CODE_DIR/edit_video.py" \
#   "$RAW/plasticity_lecture.mp4" 00:08:08 00:33:51 "$INTERM/new_vid_5.mp4" --speed 1.5 --subs "$SUBS6" "${WM6_ARGS[@]}"
# apply_image_overlay 5

# WM5=$(get_word_mark "new_vid_6.mp4")
# WM5_ARGS=(); [ -n "$WM5" ] && WM5_ARGS=(--word-mark "$WM5")
# SUBS5=$(get_subs_for_vid 6)
# python "$CODE_DIR/edit_video.py" \
#   "$RAW/plasticity_lecture.mp4" 00:37:23 00:46:35 "$INTERM/new_vid_6.mp4" --speed 1.5 --subs "$SUBS5" "${WM5_ARGS[@]}"
# apply_image_overlay 6

# ---- Single merged video (all interm clips in order) ----
python "$CODE_DIR/merge_video.py" -o "$PROCESSED/merged.mp4" \
  "$INTERM/new_vid_1.mp4" \
  "$INTERM/new_vid_2.mp4" \
  "$INTERM/new_vid_3.mp4" \
  "$INTERM/new_vid_4.mp4" 
  # \
  # "$INTERM/new_vid_5.mp4" \
  # "$INTERM/new_vid_6.mp4"

echo "Done. Merged video: $PROCESSED/merged.mp4"