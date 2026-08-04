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
    parser.add_argument("--model", required=True, help="path to the MLX-converted Surya model")
    parser.add_argument("--prompt", default="OCR this image to HTML.")
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()

    from savitr.mlx_ocr import MLXSuryaOCR

    engine = MLXSuryaOCR(mlx_path=args.model, max_tokens=args.max_tokens, prompt=args.prompt)
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
                text, _tokens = engine.ocr_image(path)
                print(json.dumps({"text": text}), flush=True)
            except Exception as exc:  # keep serving; one bad crop must not kill the run
                print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
