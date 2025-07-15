#!/bin/bash

set -euo pipefail

INPUT="Dune_t00.mkv"
BASENAME="dune"
LOGFILE="dolby_conversion.log"

log() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S.%6N%z')] [INFO] $1" | tee -a "$LOGFILE"
}

log "Starting Dolby Vision conversion pipeline"

log "Extracting video stream from input file"
ffmpeg -hide_banner -loglevel error -i "$INPUT" -map 0:v:0 -c copy "${BASENAME}_p7.hevc"

log "Converting Profile 7 to Profile 8.1"
ffmpeg -hide_banner -loglevel error -i "${BASENAME}_p7.hevc" -c:v copy -bsf:v hevc_mp4toannexb -f hevc - \
  | dovi_tool -m 2 convert --discard - -o "${BASENAME}_p81.hevc"

log "Extracting RPU from converted stream"
dovi_tool extract-rpu "${BASENAME}_p81.hevc" -o rpu_81.bin > /dev/null

log "Re-encoding video using libx265"
ffmpeg -hide_banner -loglevel error -i "${BASENAME}_p81.hevc" -an -sn -dn \
  -c:v libx265 \
  -preset slow \
  -crf 20.5 \
  -tune fastdecode \
  -g 240 -keyint_min 24 \
  -x265-params "pools=1:wpp=1" \
  "${BASENAME}_final_video.hevc"

log "Injecting RPU into re-encoded video"
dovi_tool inject-rpu -i "${BASENAME}_final_video.hevc" --rpu-in rpu_81.bin -o "${BASENAME}_final_video_with_rpu.hevc" > /dev/null

log "Extracting chapters from original file"
ffmpeg -hide_banner -loglevel error -i "$INPUT" -f ffmetadata chapters.txt

log "Remuxing final MKV with mkvmerge"
mkvmerge -o "${BASENAME}_final.mkv" \
  --language 0:eng --default-track 0:yes "${BASENAME}_final_video_with_rpu.hevc" \
  --language 0:eng --audio-tracks 1,2,3,4 --subtitle-tracks 6 \
  --chapters chapters.txt --no-video "$INPUT" >> "$LOGFILE" 2>&1

log "Pipeline completed successfully"