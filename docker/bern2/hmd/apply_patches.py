#!/usr/bin/env python3
"""Rewrite BERN2 upstream hardcoded CUDA calls to use hmd.device.DEVICE."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/app")

IMPORT_BLOCK = (
    "try:\n"
    "    from hmd.device import DEVICE as _HMD_DEVICE\n"
    "except ImportError:\n"
    "    from device import DEVICE as _HMD_DEVICE  # native PYTHONPATH=hmd\n"
)


def _ensure_import(text: str) -> str:
    if "_HMD_DEVICE" in text:
        return text
    # Insert after the first contiguous import block (respect multi-line parentheses).
    lines = text.splitlines(keepends=True)
    insert_at = 0
    seen_import = False
    paren_depth = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if paren_depth > 0:
            paren_depth += line.count("(") - line.count(")")
            insert_at = i + 1
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            seen_import = True
            paren_depth = line.count("(") - line.count(")")
            insert_at = i + 1
            continue
        if seen_import and (stripped == "" or stripped.startswith("#")):
            insert_at = i + 1
            continue
        if seen_import:
            break
    lines.insert(insert_at, "\n" + IMPORT_BLOCK + "\n")
    return "".join(lines)


def patch_file(path: pathlib.Path, replacements: list[tuple[str, str]]) -> None:
    if not path.is_file():
        print(f"skip missing {path}", file=sys.stderr)
        return
    text = path.read_text(encoding="utf-8")
    original = text
    text = _ensure_import(text)
    for old, new in replacements:
        text = text.replace(old, new)
    # Catch any remaining bare .cuda() on model/tensor call sites we care about.
    text = re.sub(r"\.cuda\(\)", ".to(_HMD_DEVICE)", text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")


def main() -> int:
    ner = ROOT / "multi_ner" / "main.py"
    patch_file(
        ner,
        [
            (
                'os.environ["CUDA_VISIBLE_DEVICES"]="0"',
                'os.environ.setdefault("CUDA_VISIBLE_DEVICES", os.environ.get("CUDA_VISIBLE_DEVICES", "0"))',
            ),
        ],
    )
    nn = ROOT / "normalizers" / "neural_normalizer.py"
    patch_file(
        nn,
        [
            (
                "return {key: torch.tensor(val[idx]).cuda() for key, val in self.encodings.items()}",
                "return {key: torch.tensor(val[idx]).to(_HMD_DEVICE) for key, val in self.encodings.items()}",
            ),
            (
                "self.model = AutoModel.from_pretrained(model_name_or_path).cuda()",
                "self.model = AutoModel.from_pretrained(model_name_or_path).to(_HMD_DEVICE)",
            ),
        ],
    )
    # Flask 3 未从 flask 导入 Response 时，错误路径会 NameError 掩盖真实错误信息
    app_init = ROOT / "app" / "__init__.py"
    if app_init.is_file():
        text = app_init.read_text(encoding="utf-8")
        original = text
        if "from flask import" in text and "Response" not in text.split("from flask import", 1)[1].split("\n", 1)[0]:
            text = text.replace(
                "from flask import Flask, render_template, request",
                "from flask import Flask, Response, render_template, request",
            )
        if text != original:
            app_init.write_text(text, encoding="utf-8")
            print(f"patched {app_init}")
        else:
            print(f"unchanged {app_init}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
