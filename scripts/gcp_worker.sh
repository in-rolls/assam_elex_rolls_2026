#!/usr/bin/env bash
#
# Startup script for a rented CPU box that prepares and reads constituencies.
#
# Three properties matter more than anything else here, and each is one line of this script.
#
#   Nothing secret lands on the box. The instance's service account is read from the metadata
#   server, so Cloud Vision is called with a short-lived token and there is no key to leak, to
#   rotate, or to find in a shell history.
#
#   The work survives the machine. A spot VM is reclaimed with no warning and its disk goes with
#   it. So state is pushed to a bucket every few minutes, and pulled back before work starts --
#   a replacement instance resumes where the last one stopped rather than re-doing, and more to
#   the point, re-paying for, the parts already read.
#
#   The log is durable and readable while it runs. Everything goes to a file that is synced with
#   the state, so a run can be watched from a laptop and post-mortemed after the box is gone.
#
# Set by the launcher through instance metadata: BUCKET, ACS, PARTS, WORKERS.
set -uo pipefail

BUCKET=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/bucket)
ACS=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/acs)
PARTS=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/parts || echo 0)
WORKERS=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/workers || echo 0)

ROOT=/mnt/work
LOG=$ROOT/run.log
mkdir -p "$ROOT"
exec > >(tee -a "$LOG") 2>&1

say() { echo "$(date -u +%H:%M:%S) | $*"; }

# One tesseract thread per worker. Tesseract uses OpenMP and will otherwise take about four
# threads inside each of the 32 worker processes: the first run on this machine sat at a load
# average of 113 on 32 cores and finished 4 parts in 37 minutes, which extrapolates to 37 hours
# for a constituency that should take half an hour. The cores are already saturated by having
# one process per core; the threads only add contention.
export OMP_THREAD_LIMIT=1
export OMP_NUM_THREADS=1

# Unbuffered, or Python block-buffers stdout into a pipe and the log shows nothing for the
# length of the run -- which is indistinguishable from a wedged job at exactly the moment you
# need to tell the difference.
export PYTHONUNBUFFERED=1

say "host $(hostname), $(nproc --all) vCPU, $(free -g | awk '/^Mem:/{print $2}') GB RAM"
[ "$WORKERS" -eq 0 ] && WORKERS=$(nproc --all)

say "installing poppler, tesseract and python"
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
# poppler renders the PDFs; tesseract reads the EPIC strip and the section headers. Both are the
# same versions the pipeline was measured against on a laptop, which is the point of naming them.
apt-get -qq install -y poppler-utils tesseract-ocr tesseract-ocr-asm tesseract-ocr-eng \
  python3-pip python3-venv git >/dev/null

say "cloning the pipeline at its current main"
git clone -q https://github.com/in-rolls/assam_elex_rolls_2026.git "$ROOT/repo"
cd "$ROOT/repo"
say "at commit $(git rev-parse --short HEAD)"
python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" -q install --upgrade pip
"$ROOT/venv/bin/pip" -q install -e ".[gcp]"

# Resume, part one: whatever a previous instance got through.
say "pulling any existing state from $BUCKET"
mkdir -p "$ROOT/stage1" "$ROOT/shards"
gsutil -q -m rsync -r "$BUCKET/stage1" "$ROOT/stage1" 2>/dev/null || true
gsutil -q -m rsync -r "$BUCKET/shards" "$ROOT/shards" 2>/dev/null || true
say "resumed with $(find "$ROOT/stage1" -name manifest.jsonl 2>/dev/null | wc -l) parts already prepared"

# Resume, part two: keep pushing, so the next instance has something to resume from. Started
# before the work rather than after it, because the failure this guards against is the work
# never reaching its own end.
(
  while true; do
    sleep 90
    gsutil -q -m rsync -r "$ROOT/stage1" "$BUCKET/stage1" 2>/dev/null
    gsutil -q -m rsync -r "$ROOT/shards" "$BUCKET/shards" 2>/dev/null
    gsutil -q cp "$LOG" "$BUCKET/run.log" 2>/dev/null
  done
) &
SYNC=$!

LIMIT=""
[ "$PARTS" -gt 0 ] && LIMIT="--limit $PARTS"

for AC in $ACS; do
  NAME="AC${AC}_ASM.zip"
  # Wait for the archive rather than skipping it. The upload from a laptop and the boot of this
  # machine are independent, and an instance that starts first should idle for a few minutes
  # instead of reporting the constituency missing and moving on -- which reads, later, exactly
  # like a constituency that had nothing in it.
  WAITED=0
  until gsutil -q stat "$BUCKET/source/$NAME" 2>/dev/null; do
    [ "$WAITED" -ge 3600 ] && break
    [ $((WAITED % 300)) -eq 0 ] && say "waiting for $NAME to appear in the bucket (${WAITED}s)"
    sleep 30; WAITED=$((WAITED + 30))
  done
  say "fetching $NAME"
  gsutil -q cp "$BUCKET/source/$NAME" "$ROOT/$NAME" || { say "no $NAME in the bucket"; continue; }

  say "running AC$AC with $WORKERS workers $LIMIT"
  # No VISION_API_KEY: the client falls back to the instance's service account.
  "$ROOT/venv/bin/python" -m electors run "$ROOT/$NAME" \
    --work "$ROOT/stage1" --shards "$ROOT/shards" \
    --manifest "$ROOT/shards/manifest.json" --workers "$WORKERS" $LIMIT
  say "AC$AC finished with status $?"

  # The zip is 1.7 GB and is not needed again; the composites are, and they stay.
  rm -f "$ROOT/$NAME"
done

kill $SYNC 2>/dev/null
say "final sync"
gsutil -q -m rsync -r "$ROOT/stage1" "$BUCKET/stage1"
gsutil -q -m rsync -r "$ROOT/shards" "$BUCKET/shards"
gsutil -q cp "$LOG" "$BUCKET/run.log"
say "done -- results under $BUCKET/shards, composites under $BUCKET/stage1"
touch "$ROOT/FINISHED"
gsutil -q cp "$ROOT/FINISHED" "$BUCKET/FINISHED"
