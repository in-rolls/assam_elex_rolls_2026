"""Generate ``notebooks/dots_kaggle.ipynb``.

Written as a generator rather than by hand because a notebook is JSON with source split into
lines, and hand-editing that is how a stray escape breaks a file nobody can open. Run this after
changing the notebook's content:

    python notebooks/build_dots_kaggle.py
"""

from __future__ import annotations

import json
from pathlib import Path

MD = "markdown"
CODE = "code"

CELLS = [
    (
        MD,
        """# dots.ocr on Kaggle — measure throughput, then check Vision

Two jobs, both small. Neither is "run the state": dots.ocr reads **one box per inference**, so
25 million electors is ~1,160 GPU-hours at the rate a P100 is likely to manage — about nine
months of Kaggle's 30 free GPU-hours a week. It also **ties** Cloud Vision on accuracy (72%
exact names each, same age, house and sex), so running it everywhere would buy no accuracy.

What it is for:

1. **Throughput.** Every cost claim about dots.ocr — including every one in this repo — rests on
   a number nobody has measured. The single-stream MLX figure from a Mac is a property of that
   laptop. This measures batched boxes/sec on a real GPU and prints the runtime that produced
   it, because quoting one runtime's number as another's is the exact mistake being corrected.
2. **A second opinion.** Once Vision has read the state nothing checks it. Two independent
   engines disagreeing is the only automatic error signal available without labels.

Nothing to upload: the crops come from the public repo. Needs **GPU** and **internet** on.""",
    ),
    (
        CODE,
        """# ---- configuration -------------------------------------------------------------
MODEL_ID = "dots-studio/dots.ocr"   # rednote-hilab/dots.ocr redirects here

# "Extract the text content from this image." -- prompt_ocr from the model's own prompts.py.
# Not the layout prompt: asked for layout, dots.ocr classifies a ruled elector grid as a single
# "Picture" and returns nothing from it.
PROMPT = "Extract the text content from this image."

# Measured, not guessed. Over 56 readings dots.ocr has already given for single boxes, the text
# is a median of 81 characters and 201 at the 90th percentile; the 255-character maximum is a
# runaway ("1936" repeated to the cap), not content. So 128 tokens would truncate a real answer
# on roughly one box in ten -- and a truncated answer looks like a bad model, which is exactly
# the mistake that scored savitr at 31% when it was 61%.
#
# The model card's 24,000 is for whole pages. A cap that large lets a runaway generate for
# seconds, which would distort the very rate this notebook exists to measure.
MAX_NEW_TOKENS = 256

THROUGHPUT_BATCHES = [4, 8, 16]   # swept, because the best batch size is not knowable in advance
THROUGHPUT_CROPS = 96             # per batch size, after a warm-up that is not timed
OUT = "/kaggle/working/dots_readings.json\"""",
    ),
    (
        CODE,
        """import glob, os, sys, time
import torch

print("torch", torch.__version__, "| cuda", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise SystemExit("No GPU. Settings -> Accelerator -> GPU T4 x2 (or P100).")

CAPABILITY = torch.cuda.get_device_capability()
print("gpu", torch.cuda.get_device_name(0), "| compute capability", CAPABILITY)

# The model card uses bfloat16 + flash_attention_2. Both need Ampere (8.0+); Kaggle's T4 is 7.5
# and its P100 is 6.0, so following the card verbatim fails on the hardware this runs on.
AMPERE = CAPABILITY[0] >= 8
DTYPE = torch.bfloat16 if AMPERE else torch.float16
ATTN = "flash_attention_2" if AMPERE else "sdpa"
print(f"using dtype={DTYPE} attn={ATTN}")

# Kaggle hands out a P100 (sm_60) or a T4 x2 (sm_75) and the API cannot ask for one. Kaggle's
# own preinstalled torch dropped sm_60 -- "supports sm_70 ... sm_120" -- so a P100 draw is a
# lost run rather than a slow one, and it should say so here rather than fail cryptically deep
# inside generate().
SUPPORTED = torch.cuda.get_arch_list()
ARCH = f"sm_{CAPABILITY[0]}{CAPABILITY[1]}"
print("torch was built for:", " ".join(SUPPORTED))
if ARCH not in SUPPORTED:
    # Raised here rather than left to fail later: the first CUDA op inside generate() dies with
    # an opaque AcceleratorError three minutes and a 3 GB model download later. This costs 30
    # seconds and says what to do.
    raise SystemExit(
        f"{ARCH} ({torch.cuda.get_device_name(0)}) is not in torch's arch list, so this GPU "
        f"cannot run the installed torch. Kaggle assigns P100 or T4 at random and the API "
        f"cannot ask for one -- resubmit until a T4 comes up."
    )""",
    ),
    (
        CODE,
        """!pip -q install -U "transformers>=4.51" accelerate qwen-vl-utils 2>&1 | tail -2""",
    ),
    (
        CODE,
        """# The crops live in the public repo, so there is nothing to upload or attach. A shallow clone
# rather than a tarball with --wildcards, which is GNU tar only and silently extracts nothing
# on a BSD tar -- a difference that would only show up here, on someone else's machine.
!rm -rf /kaggle/working/repo
!git clone --depth 1 -q https://github.com/in-rolls/assam_elex_rolls_2026 /kaggle/working/repo

CROPS = sorted(glob.glob("/kaggle/working/repo/dataset/dots_bench/*.png"))
print(f"{len(CROPS):,} crops")
if not CROPS:
    raise SystemExit("No crops -- is internet enabled for this notebook?")
print("first few:", [os.path.basename(p) for p in CROPS[:3]])""",
    ),
    (
        CODE,
        """from transformers import AutoModelForCausalLM, AutoProcessor

t0 = time.time()
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    attn_implementation=ATTN,
    torch_dtype=DTYPE,
    device_map="auto",
    trust_remote_code=True,
)
model.eval()
processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
# Decoder-only batched generation must pad on the left, or short prompts emit from padding.
processor.tokenizer.padding_side = "left"
print(f"loaded in {time.time() - t0:.0f}s")
RUNTIME = f"transformers {__import__('transformers').__version__}, {ATTN}, {str(DTYPE).split('.')[-1]}\"""",
    ),
    (
        CODE,
        """import re

from PIL import Image
from qwen_vl_utils import process_vision_info

# Keys the processor produces that this model's generate() will not accept. Seeded with the one
# already seen and grown at runtime from whatever generate() complains about.
DROP = {"mm_token_type_ids"}


def read_batch(paths, max_new_tokens=MAX_NEW_TOKENS):
    \"\"\"One batch of crops in, one string of text out per crop, in the order given.\"\"\"
    messages = [
        [{"role": "user", "content": [
            {"type": "image", "image": Image.open(p).convert("RGB")},
            {"type": "text", "text": PROMPT},
        ]}]
        for p in paths
    ]
    texts = [
        processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
        for m in messages
    ]
    images, videos = [], []
    for m in messages:
        got_images, got_videos = process_vision_info(m)
        images.extend(got_images or [])
        videos.extend(got_videos or [])

    inputs = processor(
        text=texts, images=images, videos=videos or None, padding=True, return_tensors="pt"
    ).to(model.device)
    inputs = {k: v for k, v in inputs.items() if k not in DROP}

    with torch.inference_mode():
        try:
            out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        except ValueError as exc:
            # The processor and the model's remote code come from different versions, so the
            # processor emits keys generate() will not take -- 'mm_token_type_ids' on the
            # transformers of the day. Rather than pin a version that will drift again, take
            # the names out of the complaint, remember them, and retry once.
            unused = re.findall(r"'([A-Za-z_]+)'", str(exc)) if "not used by the model" in str(exc) else []
            if not unused:
                raise
            print(f"dropping keys the model does not accept: {unused}")
            DROP.update(unused)
            inputs = {k: v for k, v in inputs.items() if k not in DROP}
            out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

    trimmed = [o[len(i):] for i, o in zip(inputs["input_ids"], out)]
    return processor.batch_decode(trimmed, skip_special_tokens=True)""",
    ),
    (
        MD,
        """## Sanity check first

One crop, printed. If the processor call needs a tweak for this model you find out here, in
twenty seconds, rather than after half an hour of timing runs.""",
    ),
    (
        CODE,
        """sample = read_batch(CROPS[:1])
print(repr(sample[0]))""",
    ),
    (
        MD,
        """## Throughput

Swept over batch sizes, with a warm-up excluded from the timing — the first call pays for
CUDA graph capture and weight paging, and counting it understates the rate.""",
    ),
    (
        CODE,
        """read_batch(CROPS[:2])          # warm-up, deliberately not timed
torch.cuda.synchronize()

results = {}
for size in THROUGHPUT_BATCHES:
    n = min(THROUGHPUT_CROPS, len(CROPS))
    chosen = CROPS[:n]
    started = time.time()
    done = 0
    for i in range(0, n, size):
        read_batch(chosen[i:i + size])
        done += len(chosen[i:i + size])
    torch.cuda.synchronize()
    seconds = time.time() - started
    results[size] = done / seconds
    print(f"batch {size:>3}: {done} crops in {seconds:6.1f}s = {results[size]:5.2f} boxes/sec")

BEST = max(results, key=results.get)
RATE = results[BEST]
print(f"\\nbest {RATE:.2f} boxes/sec at batch {BEST}")
print(f"runtime: {RUNTIME}")
print(f"gpu: {torch.cuda.get_device_name(0)}")""",
    ),
    (
        CODE,
        """ELECTORS = 24_958_139
hours = ELECTORS / RATE / 3600
print(f"MEASURED at {RATE:.2f} boxes/sec on {torch.cuda.get_device_name(0)} ({RUNTIME})")
print(f"  whole state : {hours:,.0f} GPU-hours")
print(f"  Kaggle free : {hours / 30:,.0f} weeks at 30 GPU-hours/week")
for rent in (1.0, 2.0):
    print(f"  rented at ${rent:.2f}/hr : ${hours * rent:,.0f}")
print()
print("Cloud Vision reads the same 25M electors for $368 in about a day of wall clock,")
print("and scores the same. This number decides whether dots.ocr is worth it as an")
print("escalation engine on flagged rows -- not whether it should read everything.")
print()
print("Quote this rate WITH the runtime and GPU above. transformers is a lower bound:")
print("vLLM with continuous batching is typically 2-5x faster for this shape of job.")""",
    ),
    (
        MD,
        """## Read the sample

Writes `dots_readings.json` as `{crop filename: text}` to `/kaggle/working/`. Download it and
run locally:

```python
import json
from pathlib import Path
from electors import crops, resolution
readings = json.loads(Path("dots_readings.json").read_text())
arms = crops.readings_to_arm(readings) + resolution.load(Path("vision_arms.json"))
print(resolution.report(arms))
```

`resolution.agreement()` and `resolution.blank_rates()` then compare it against Vision — as
**agreement**, never as accuracy. Two engines can be wrong together.""",
    ),
    (
        CODE,
        """import json

readings, started = {}, time.time()
for i in range(0, len(CROPS), BEST):
    batch = CROPS[i:i + BEST]
    try:
        for path, text in zip(batch, read_batch(batch)):
            readings[os.path.basename(path)] = text
    except Exception as exc:                      # one bad batch must not lose the run
        print(f"batch at {i} failed: {type(exc).__name__}: {exc}")
    if (i // BEST) % 20 == 0:
        rate = len(readings) / max(1e-9, time.time() - started)
        left = (len(CROPS) - len(readings)) / max(rate, 1e-9) / 60
        print(f"{len(readings):>6,}/{len(CROPS):,}  {rate:5.2f}/s  ~{left:.0f} min left")
        # Written as it goes: a 12-hour session limit should never cost the whole run.
        with open(OUT, "w", encoding="utf-8") as handle:
            json.dump(readings, handle, ensure_ascii=False)

with open(OUT, "w", encoding="utf-8") as handle:
    json.dump(readings, handle, ensure_ascii=False)
print(f"\\nwrote {len(readings):,} readings to {OUT}")""",
    ),
]


def build() -> dict:
    return {
        "cells": [
            {
                "cell_type": kind,
                "metadata": {},
                "source": text.splitlines(keepends=True),
                **({"outputs": [], "execution_count": None} if kind == CODE else {}),
            }
            for kind, text in CELLS
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "accelerator": "GPU",
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    target = Path(__file__).with_name("dots_kaggle.ipynb")
    target.write_text(json.dumps(build(), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {target} ({len(CELLS)} cells)")
