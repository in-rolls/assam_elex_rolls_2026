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

THROUGHPUT_BATCHES = [1, 2, 4, 8]  # swept; a T4 ran out of memory above 4 at this crop size
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
        """# Pinned, not ">=". dots.ocr's own requirements.txt says transformers==4.56.1, and its
# remote modeling code is written against that generation's generate() internals: on a newer
# transformers, prepare_inputs_for_generation is called without cache_position and the model's
# `if cache_position[0] == 0:` dies with "'NoneType' object is not subscriptable". An unpinned
# install cost a whole run to find that.
!pip -q install "transformers==4.56.1" accelerate qwen-vl-utils 2>&1 | tail -3
import transformers
print("transformers", transformers.__version__)
assert transformers.__version__.startswith("4.56"), "restart the kernel; an older import is live\"""",
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
        """from huggingface_hub import snapshot_download
from transformers import AutoModelForCausalLM, AutoProcessor

# Downloaded to a directory with **no period in its name**, then loaded from that path rather
# than from the hub id. With trust_remote_code, transformers imports the model's own code as
# `transformers_modules.<org>.<name>`, and "dots.ocr" makes that a package path -- it looks for
# a module `dots` and fails with:
#
#     ModuleNotFoundError: No module named 'transformers_modules.dots-studio.dots'
#
# The model's own README says to use a directory without periods, and its download tool writes
# to ./weights/DotsOCR for exactly this reason.
# Outside /kaggle/working, which *is* the kernel's output: with the weights in there,
# `kaggle kernels output` downloads 4 GB of safetensors before it reaches the log, and the log
# is the only thing worth having.
MODEL_DIR = "/tmp/DotsOCR"
snapshot_download(repo_id=MODEL_ID, local_dir=MODEL_DIR)

t0 = time.time()
model = AutoModelForCausalLM.from_pretrained(
    MODEL_DIR,
    attn_implementation=ATTN,
    torch_dtype=DTYPE,
    device_map="auto",
    trust_remote_code=True,
)
model.eval()


def build_processor(model_dir):
    \"\"\"The model's own processor, or the base class it forgot to finish wiring up.

    dots.ocr ships DotsVLProcessor, whose __init__ does:

        super().__init__(image_processor, tokenizer, chat_template=chat_template)

    but Qwen2_5_VLProcessor takes **video_processor** as its third positional argument, so it
    arrives as None and transformers rejects it:

        TypeError: Received a NoneType for argument video_processor,
                   but a BaseVideoProcessor was expected.

    That is upstream and true at the very version dots.ocr pins. DotsVLProcessor adds nothing
    but two token attributes, so the base processor with those set is the same thing.
    \"\"\"
    try:
        return AutoProcessor.from_pretrained(model_dir, trust_remote_code=True)
    except TypeError as exc:
        if "video_processor" not in str(exc):
            raise
        print(f"DotsVLProcessor is out of step with transformers: {exc}")
        print("building Qwen2_5_VLProcessor directly")

    import json as _json

    from transformers import AutoImageProcessor, AutoTokenizer, Qwen2_5_VLProcessor

    # Qwen2**VL**VideoProcessor, from models/qwen2_vl -- there is no video processor under
    # qwen2_5_vl at 4.56.1, and Qwen2_5_VLProcessor declares video_processor_class =
    # "AutoVideoProcessor" rather than naming a concrete one. Guessed wrong once; checked the
    # tagged source the second time.
    video, tried = None, []
    for path, name in (
        ("transformers.models.qwen2_vl.video_processing_qwen2_vl", "Qwen2VLVideoProcessor"),
        ("transformers", "Qwen2VLVideoProcessor"),
        ("transformers", "AutoVideoProcessor"),
    ):
        try:
            module = __import__(path, fromlist=[name])
            found = getattr(module, name)
            video = found.from_pretrained(model_dir) if name == "AutoVideoProcessor" else found()
            print(f"video processor: {path}.{name}")
            break
        except Exception as exc:
            tried.append(f"{path}.{name}: {type(exc).__name__}")
    if video is None:
        raise SystemExit("no usable video processor. tried:\\n  " + "\\n  ".join(tried))

    tokenizer = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    images = AutoImageProcessor.from_pretrained(model_dir, trust_remote_code=True)
    with open(f"{model_dir}/chat_template.json", encoding="utf-8") as handle:
        template = _json.load(handle)["chat_template"]
    built = Qwen2_5_VLProcessor(
        image_processor=images, tokenizer=tokenizer, video_processor=video, chat_template=template
    )
    # The only things DotsVLProcessor adds on top of its base.
    built.image_token = getattr(tokenizer, "image_token", "<|imgpad|>")
    built.image_token_id = getattr(tokenizer, "image_token_id", 151665)
    return built


processor = build_processor(MODEL_DIR)

# The vision tower casts its input to bfloat16 unconditionally:
#
#     def forward(self, hidden_states, grid_thw, bf16=True):
#         if bf16:
#             hidden_states = hidden_states.bfloat16()
#
# and it is called internally as `self.vision_tower(pixel_values, grid_thw)`, so the default
# always wins. With float16 weights that is
#
#     RuntimeError: Input type (c10::BFloat16) and bias type (c10::Half) should be the same
#
# Loading the whole model in bfloat16 would also fix it, and would be the wrong fix here: a T4
# has no bf16 tensor cores, so the rate measured would describe an emulated path rather than
# what this card can do. Pinning bf16=False keeps everything in float16, which T4s run natively.
if not AMPERE:
    _tower = model.vision_tower

    def _no_bf16_cast(hidden_states, grid_thw, bf16=False, _forward=_tower.forward):
        return _forward(hidden_states, grid_thw, bf16=False)

    _tower.forward = _no_bf16_cast
    print("vision tower pinned to float16 (its default casts to bfloat16)")
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
    # The processor emits float32 pixel values while the weights are float16, and with the
    # vision tower's own bfloat16 cast disabled nothing in between reconciles them:
    #
    #     RuntimeError: Input type (float) and bias type (c10::Half) should be the same
    #
    # Only the floating tensors. input_ids, attention_mask and grid_thw are integer, and casting
    # those would corrupt them quietly rather than fail loudly.
    inputs = {
        k: (v.to(model.dtype) if torch.is_tensor(v) and v.is_floating_point() else v)
        for k, v in inputs.items()
    }

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
    try:
        for i in range(0, n, size):
            read_batch(chosen[i:i + size])
            done += len(chosen[i:i + size])
    except torch.cuda.OutOfMemoryError:
        # A T4 has 16 GB and these crops are ~1000x450, so the batch that fits is small. An OOM
        # is a result about this card, not a failure of the run -- record it and keep the sizes
        # that did fit, rather than losing the sweep and the readings after it.
        torch.cuda.empty_cache()
        print(f"batch {size:>3}: out of memory, skipped")
        continue
    torch.cuda.synchronize()
    seconds = time.time() - started
    results[size] = done / seconds
    print(f"batch {size:>3}: {done} crops in {seconds:6.1f}s = {results[size]:5.2f} boxes/sec")

if not results:
    raise SystemExit("no batch size fitted in memory")

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
