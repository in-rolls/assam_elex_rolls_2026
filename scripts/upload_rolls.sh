#!/usr/bin/env bash
#
# Move roll archives from a laptop to the bucket one at a time, keeping local disk flat.
#
# Uploads to Cloud Storage rather than to the machine doing the work. The VM is disposable --
# preempted, recreated, deleted -- and anything sitting on it goes with it, whereas the bucket
# outlives every instance and is read back at GCP-internal speed rather than over a home uplink.
#
# Each archive is uploaded, its size confirmed against the local file, and only then deleted
# locally. A failed or short upload leaves the local copy alone, because the source of these is
# a manual download that would have to be repeated by hand.
#
#   scripts/upload_rolls.sh data/ac_rolls gs://sawasdee-assam-rolls [--keep]
set -uo pipefail

DIR=${1:-data/ac_rolls}
BUCKET=${2:-gs://sawasdee-assam-rolls}
KEEP=${3:-}

say() { echo "$(date +%H:%M:%S) | $*"; }

# --watch keeps the loop alive so archives can be dropped into the directory while it runs.
# Downloading 374 GB is days of somebody's attention in batches, and having to remember to
# restart an uploader between batches is exactly the kind of step that gets forgotten at 2am.
WATCH=""
[ "${KEEP}" = "--watch" ] && { WATCH=yes; KEEP=""; }

while true; do
# find, not a glob. Under nullglob an unmatched pattern disappears entirely, so `ls -1 $DIR/*.zip`
# becomes a bare `ls -1` -- which lists the current directory. Once every archive had been
# uploaded and deleted, this script started earnestly trying to upload `electors`, `tests` and
# `models` to the bucket.
#
# Version sorted, so AC1 precedes AC10 precedes AC100; plain sort interleaves them.
for path in $(find "$DIR" -maxdepth 1 -name '*.zip' 2>/dev/null | sort -V); do
  name=$(basename "$path")
  # Space in a name means a browser's duplicate download, and a space in an object name breaks
  # every shell path that touches it later. Skipped rather than renamed: the original is there.
  case "$name" in *" "*) say "skipping $name -- a duplicate download"; continue;; esac

  local_size=$(stat -f %z "$path" 2>/dev/null || stat -c %s "$path")
  remote_size=$(gcloud storage ls -l "$BUCKET/source/$name" 2>/dev/null | awk 'NR==1{print $1}')

  if [ "$remote_size" = "$local_size" ]; then
    say "$name already in the bucket at the right size"
  else
    say "uploading $name ($(echo "$local_size" | awk '{printf "%.1f GB", $1/1e9}'))"
    if ! gcloud storage cp "$path" "$BUCKET/source/$name" 2>&1 | tail -1; then
      say "  upload failed -- keeping the local copy"
      continue
    fi
    remote_size=$(gcloud storage ls -l "$BUCKET/source/$name" 2>/dev/null | awk 'NR==1{print $1}')
    if [ "$remote_size" != "$local_size" ]; then
      say "  size mismatch: local $local_size, bucket ${remote_size:-none} -- keeping local copy"
      continue
    fi
  fi

  if [ "$KEEP" = "--keep" ]; then
    say "  verified; keeping the local copy as asked"
  else
    rm -f "$path"
    say "  verified and removed locally; $(df -h . | awk 'NR==2{print $4}') free"
  fi
done
  [ -z "$WATCH" ] && break
  # A partially-downloaded file has no .zip extension yet, so an idle pass means the browser is
  # between batches rather than mid-file.
  sleep 60
done
say "done"
