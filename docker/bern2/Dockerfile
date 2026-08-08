# syntax=docker/dockerfile:1
# BERN2 self-hosted — CPU (linux/arm64|amd64, macOS Docker) or CUDA (linux/amd64)
# Build: task foundation:up:bern2  （自动探测 BERN2_ACCEL=cpu|cuda）
# 手动: docker build --build-arg BERN2_ACCEL=cuda -t hmd-bern2 .
ARG BERN2_ACCEL=cpu

# ----- CUDA base (Linux + NVIDIA) -----
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04 AS base-cuda
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      curl git default-jre-headless build-essential \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*
ENV BERN2_ACCEL=cuda

# ----- CPU base (Apple Silicon Docker / Linux without GPU) -----
FROM python:3.9-slim AS base-cpu
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl git default-jre-headless build-essential \
    && rm -rf /var/lib/apt/lists/*
ENV BERN2_ACCEL=cpu

# ----- Select base via BERN2_ACCEL -----
FROM base-${BERN2_ACCEL} AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt:/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Torch first (index depends on accel), then BERN2 requirements with faiss-cpu
ARG BERN2_ACCEL=cpu
ARG BERN2_TORCH_CUDA=cu118
COPY upstream/requirements.txt /tmp/bern2-requirements.txt
RUN set -eux; \
    python -m pip install --upgrade pip setuptools wheel; \
    if [ "$BERN2_ACCEL" = "cuda" ]; then \
      python -m pip install --no-cache-dir \
        "torch==2.1.2" --index-url "https://download.pytorch.org/whl/${BERN2_TORCH_CUDA}"; \
    else \
      python -m pip install --no-cache-dir \
        "torch==2.1.2" --index-url "https://download.pytorch.org/whl/cpu"; \
    fi; \
    # pin portable CPU faiss; drop bare "faiss" from upstream reqs
    grep -viE '^(faiss|torch)([=<>]|$)' /tmp/bern2-requirements.txt > /tmp/reqs.txt; \
    python -m pip install --no-cache-dir -r /tmp/reqs.txt "faiss-cpu>=1.7.4,<1.9"

COPY upstream/ /app/
COPY hmd/ /opt/hmd/
RUN python /opt/hmd/apply_patches.py /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8888
EXPOSE 18888 18892 18894 18895 18896
ENTRYPOINT ["/entrypoint.sh"]
