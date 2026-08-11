#!/usr/bin/env bash
#
# Runs on the ~30 seconds of notice GCE gives before a spot instance is reclaimed.
#
# The periodic sync in gcp_worker.sh is not enough on its own. A part takes minutes to prepare,
# so a machine can be taken having done real work that no sync interval happened to capture --
# the first trial was preempted 2.5 minutes in and lost everything, which reads afterwards
# exactly like a machine that never started.
#
# So this flushes what exists. Manifests and rows are small; the composites are the bulk and are
# copied last, because a part whose rows survived is a part that never has to be paid for again
# even if its image has to be rebuilt.
set -uo pipefail

BUCKET=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/bucket)
ROOT=/mnt/work

echo "$(date -u +%H:%M:%S) | preemption notice -- flushing state" >> "$ROOT/run.log"
gcloud storage cp "$ROOT/run.log" "$BUCKET/run.log" 2>/dev/null

# Rows first: they are what was paid for.
gcloud storage rsync -r "$ROOT/stage1" "$BUCKET/stage1" \
  --include-managed-folders 2>/dev/null &
gcloud storage rsync -r "$ROOT/shards" "$BUCKET/shards" 2>/dev/null
wait
echo "$(date -u +%H:%M:%S) | flush done" >> "$ROOT/run.log"
gcloud storage cp "$ROOT/run.log" "$BUCKET/run.log" 2>/dev/null
