"""Long-lived Surya OCR worker, run inside the savitr venv.

Surya's stack conflicts with the main venv (savitr needs mlx-vlm; the Manipur benchmark
in ``parse_unsearchable_rolls`` hit the same problem and kept a separate
``.venv-surya13``). So it runs as a subprocess and talks over stdin/stdout.

Protocol -- one JSON object per line, both directions::

    in   {"png": "<base64>"}
    out  {"text": "<ocr output>"}          or  {"error": "..."}

The model is loaded once at startup, which is the whole point: a fresh process per crop
would pay ~0.8s of model load for ~0.3s of work.

Usage::

    .venv-surya/bin/python scripts/surya_worker.py --model /path/to/surya-mlx-4bit
"""

import argparse
import base64
import json
import os
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="path to an MLX-converted Surya model")
    parser.add_argument(
        "--terse",
        action="store_true",
        help="use savitr's distilled roll model, fetched from Hugging Face if absent",
    )
    parser.add_argument("--prompt", default=None)
    # 160 is enough for one elector and short enough to cut a runaway loose quickly. Eleven of
    # thirty generations repeated themselves until stopped, so the cap is a guard and not just
    # a budget -- the parent is told the count so it can tell a truncated answer from a whole
    # one.
    parser.add_argument("--max-tokens", type=int, default=160)
    args = parser.parse_args()

    import contextlib

    from savitr.mlx_ocr import MLXSuryaOCR

    if args.terse:
        from savitr.rolls import TERSE_PROMPT, resolve_terse_model

        with contextlib.redirect_stdout(sys.stderr):
            model = args.model or resolve_terse_model()
        prompt = args.prompt or TERSE_PROMPT
    else:
        model, prompt = args.model, args.prompt or "OCR this image to HTML."
    if not model:
        parser.error("--model is required unless --terse is given")

    # savitr writes progress to stdout while it resolves and loads the model, which is the
    # same channel this protocol speaks on -- the parent read "fetching terse model ..." where
    # it expected the ready object and killed the run. Anything the loader says goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        engine = MLXSuryaOCR(mlx_path=model, max_tokens=args.max_tokens, prompt=prompt)
    # Signal readiness only after the model is resident, so the parent can block on it.
    print(json.dumps({"ready": True}), flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "cell.png")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                with open(path, "wb") as handle:
                    handle.write(base64.b64decode(payload["png"]))
                text, tokens = engine.ocr_image(path)
                print(
                    json.dumps({"text": text, "tokens": tokens, "capped": tokens >= args.max_tokens}),
                    flush=True,
                )
            except Exception as exc:  # keep serving; one bad crop must not kill the run
                print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
