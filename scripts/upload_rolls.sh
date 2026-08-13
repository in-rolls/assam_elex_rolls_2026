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
#: The published part count per constituency -- the only number here this pipeline did not
#: produce, and so the only one that can call a download incomplete.
INDEX=$(cd "$(dirname "$0")/.." && pwd)/dataset/parts.jsonl.gz
KEEP=${3:-}

say() { echo "$(date +%H:%M:%S) | $*"; }

# Split each archive across parallel streams, and ride out the bucket pushing back.
#
# A single resumable stream was tried, to make fewer requests while seven machines were hammering
# the same bucket, and it measured 120 kiB/s -- against about 2 MB/s for the default split. That
# is a seventeen-fold loss to dodge an error that retries handle, so the trade was backwards: the
# 503s are transient and the slow path is permanent.
#
# So: keep the split, and be patient instead. Retries with backoff cost nothing when the bucket is
# healthy and are the whole answer when it is not.
export CLOUDSDK_STORAGE_MAX_RETRIES=32
export CLOUDSDK_STORAGE_BASE_RETRY_DELAY=2
export CLOUDSDK_STORAGE_MAX_RETRY_DELAY=60

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
  # Delete nothing that is not a roll archive. The name is checked here, immediately before the
  # only destructive step in this script, rather than trusted from however the list was built --
  # because the list was built wrongly once. A glob that matched nothing became a bare `ls -1`,
  # which listed the working directory, and this script uploaded README.md, LICENSE, Makefile,
  # DATA.md and pyproject.toml to the bucket and then deleted all five. They were in git; that
  # was luck, not design.
  case "$name" in
    AC[0-9]*.zip) ;;
    *) say "ignoring $name -- not a roll archive"; continue;;
  esac
  # Space in a name means a browser's duplicate download, and a space in an object name breaks
  # every shell path that touches it later. Skipped rather than renamed: the original is there.
  case "$name" in *" "*) say "skipping $name -- a duplicate download"; continue;; esac

  # A floor before anything clever. Every archive seen so far is between 1.66 and 3.5 GB, and the
  # truncated ones were 0.36 to 0.56 -- so anything under a gigabyte is a failed download, whatever
  # its central directory happens to say. This runs first because it costs a stat, and because the
  # check below depends on the parts index being present and readable, while this one depends on
  # nothing.
  bytes=$(stat -f %z "$path" 2>/dev/null || stat -c %s "$path")
  if [ "${bytes:-0}" -lt 1000000000 ]; then
    say "REFUSING $name -- $(echo "$bytes" | awk '{printf "%.2f GB", $1/1e9}'), under the 1 GB floor; incomplete download"
    continue
  fi

  # Does the archive hold one PDF per part the roll publishes?
  #
  # Comparing the local file to the bucket copy, which is all this script used to do, cannot
  # answer that: a download truncated to a seventh of its length uploads perfectly and matches
  # itself perfectly at the far end. Three such archives reached this directory looking finished
  # -- AC88, AC94 and AC95, at 1.5 MB a part against a normal 9 to 15 -- and only did not reach
  # the bucket because the uploader is serial and had not got to them.
  #
  # The count is the check rather than the size, because size is a guess about a constituency
  # whose real size is unknown, while the part count is published on the roll's own info pages and
  # is not something this pipeline produced. `unzip -l` reads the central directory only, so this
  # costs milliseconds even on three gigabytes.
  ac=$(echo "$name" | sed -E 's/^AC0*([0-9]+)_.*/\1/')
  # Resolved from this script's own location, not the working directory: the uploader is run from
  # wherever it is convenient, and a check that silently finds no index is a check that passes
  # everything.
  want=$(python3 -c "
import gzip, json
print(sum(1 for l in gzip.open('$INDEX','rt') if json.loads(l).get('ac_no')==$ac))
" 2>/dev/null || echo 0)
  got=$(unzip -l "$path" 2>/dev/null | grep -ci '\.pdf$')
  if [ "${want:-0}" -gt 0 ] && [ "$got" != "$want" ]; then
    say "REFUSING $name -- $got PDFs for $want published parts; incomplete download, keeping it local"
    continue
  fi

  local_size=$(stat -f %z "$path" 2>/dev/null || stat -c %s "$path")
  remote_size=$(gcloud storage ls -l "$BUCKET/source/$name" 2>/dev/null | awk 'NR==1{print $1}')

  if [ "$remote_size" = "$local_size" ]; then
    say "$name already in the bucket at the right size"
  else
    say "uploading $name ($(echo "$local_size" | awk '{printf "%.1f GB", $1/1e9}'))"
    # Whole output, not the last line. Trimming it to one line is what turned "HTTPError 503
    # on nineteen component uploads" into a stray fragment of gcloud's help text, and three
    # separate wrong diagnoses were made from that fragment.
    if ! gcloud storage cp "$path" "$BUCKET/source/$name" 2>&1; then
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
