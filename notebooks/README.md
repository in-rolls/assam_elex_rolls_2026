# Running the dots.ocr benchmark on Kaggle

    uv pip install kaggle
    export KAGGLE_API_TOKEN=<a token from kaggle.com/settings>
    python notebooks/build_dots_kaggle.py          # regenerate the notebook
    cp notebooks/dots_kaggle.ipynb /tmp/k/dots-ocr-throughput.ipynb
    cp notebooks/kernel-metadata.json /tmp/k/
    kaggle kernels push -p /tmp/k
    kaggle kernels status soodoku/dots-ocr-throughput
    kaggle kernels output soodoku/dots-ocr-throughput -p out/kaggle

Kaggle runs it; no browser needed. Nothing to upload either -- the crops come from
`dataset/dots_bench/` in this public repo via a shallow clone.

## Three things that cost a run each to find

**`machine_shape` is what picks the GPU.** `enable_gpu: true` alone draws a P100 or a T4 at
Kaggle's discretion, and **Kaggle's own preinstalled torch no longer supports the P100** -- it
reports `sm_70 ... sm_120`, and a P100 is `sm_60`. Two runs died that way. The field is
documented on the session request rather than the push request, but `kernels push` reads it
straight out of `kernel-metadata.json`:

    "machine_shape": "NvidiaTeslaT4"

**The model card's settings do not run on Kaggle hardware.** It uses `flash_attention_2` and
`bfloat16`, both of which need Ampere (8.0+). A T4 is 7.5. The notebook picks `sdpa` and
`float16` from the detected compute capability.

**The processor emits keys the model will not take.** `mm_token_type_ids`, on the transformers
of the day -- the processor and the model's remote code are versioned separately. Rather than
pin a version that will drift again, the notebook reads the rejected names out of the error,
remembers them, and retries once.

## Why the notebook is generated

`build_dots_kaggle.py` writes `dots_kaggle.ipynb`. A notebook is JSON with source split into
lines, and hand-editing that is how a stray escape breaks a file nobody can open -- which
happened once here, to an `f"\n..."` that the generator interpreted instead of emitting.
