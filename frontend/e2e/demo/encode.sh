#!/bin/sh
# Encodes the README demo media from what demo.spec.ts recorded (see the comment at its top).
#   sh frontend/e2e/demo/encode.sh <DEMO_OUT dir> <media dir>
# Writes <media>/demo.mp4 (1440x900 H.264, yuv420p, +faststart), <media>/demo-preview.gif
# (960 px wide, palette pass) and <media>/screenshots/*.png (lossless oxipng). ffmpeg and oxipng
# run in throwaway containers; nothing is installed on the host.
set -eu

[ $# -eq 2 ] || { echo "usage: sh encode.sh <DEMO_OUT dir> <media dir>" >&2; exit 2; }
mkdir -p "$2/screenshots"
# Docker on Windows wants C:/... paths; Git Bash's `pwd -W` gives them, elsewhere plain `pwd`.
abs() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }
WORK=$(abs "$1")
MEDIA=$(abs "$2")
FFMPEG_IMAGE=${FFMPEG_IMAGE:-linuxserver/ffmpeg:9.0.2-cli-ls86}
export MSYS_NO_PATHCONV=1

ffmpeg() { docker run --rm -v "$WORK:/work" -v "$MEDIA:/media" -w /work "$FFMPEG_IMAGE" -hide_banner -loglevel error -y "$@"; }

# 1. The walkthrough: the kept cuts at their real timing, resampled to 30 fps (JPEG frames are
#    full range; convert to the limited-range yuv420p players expect).
ffmpeg -f concat -safe 0 -i video.ffconcat -fps_mode cfr -r 30 \
  -vf "scale=in_range=full:out_range=limited,format=yuv420p" -color_range tv \
  -c:v libx264 -preset slow -crf 22 -tune stillimage -pix_fmt yuv420p -movflags +faststart /media/demo.mp4

# 2. The preview loop: the clips listed in preview.txt ("start length" per line), joined.
filter=""
labels=""
n=0
while read -r start length; do
  [ -n "$start" ] || continue
  filter="$filter[0:v]trim=start=$start:duration=$length,setpts=PTS-STARTPTS,fps=12,scale=960:-1:flags=lanczos[c$n];"
  labels="$labels[c$n]"
  n=$((n + 1))
done < "$1/preview.txt"
filter="${filter}${labels}concat=n=$n:v=1:a=0,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
ffmpeg -i /media/demo.mp4 -filter_complex "$filter" -loop 0 /media/demo-preview.gif

# 3. The screenshots, losslessly recompressed.
cp "$1"/screenshots/*.png "$2/screenshots/"
docker run --rm -v "$MEDIA/screenshots:/s" alpine:3.20 sh -c "apk add --no-cache -q oxipng && oxipng -q -o 4 --strip safe /s/*.png"

ls -l "$2" "$2/screenshots"
