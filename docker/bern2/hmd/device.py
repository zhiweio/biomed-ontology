"""BERN2 device resolution: CUDA → MPS → CPU.

Official BERN2 hardcodes ``.cuda()``; patches rewrite those call sites to use
``hmd.device.DEVICE`` so the same image works on Linux+NVIDIA and CPU/MPS hosts.
"""

from __future__ import annotations

import os

import torch

__all__ = ["DEVICE", "device_name", "resolve_device"]


def resolve_device(prefer: str | None = None) -> torch.device:
    prefer = (prefer or os.environ.get("BERN2_DEVICE") or "auto").strip().lower()
    if prefer in {"cuda", "gpu"}:
        if not torch.cuda.is_available():
            raise RuntimeError("BERN2_DEVICE=cuda but torch.cuda.is_available() is False")
        return torch.device("cuda")
    if prefer == "mps":
        if not getattr(torch.backends, "mps", None) or not torch.backends.mps.is_available():
            raise RuntimeError("BERN2_DEVICE=mps but MPS is unavailable")
        return torch.device("mps")
    if prefer == "cpu":
        return torch.device("cpu")
    # auto
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


DEVICE = resolve_device()


def device_name() -> str:
    if DEVICE.type == "cuda":
        try:
            return f"cuda:{torch.cuda.get_device_name(0)}"
        except Exception:
            return "cuda"
    return DEVICE.type
