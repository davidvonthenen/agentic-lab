# Linux image: builds natively on linux/amd64 and linux/arm64.
# Windows and macOS run this Linux image through their container engine's VM.
FROM docker.io/library/python:3.12-slim-bookworm

# The publisher sets these constants in docker-compose.yml, not in a secret file.
ARG LAB_REPO_URL=https://github.com/davidvonthenen/agentic-lab
ARG LAB_REPO_REF=main

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    HF_HOME=/home/lab/.cache/huggingface \
    LAB_REPO_DIR=/workspace/agentic-rag \
    LAB_CONTAINER=true \
    EMBEDDING_DEVICE=cpu \
    EMBEDDING_TORCH_DTYPE=float32 \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
    GIT_TERMINAL_PROMPT=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash build-essential ca-certificates curl git libgomp1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && useradd --create-home --uid 1000 --shell /bin/bash lab \
    && mkdir -p /opt/lab /workspace/agentic-rag /home/lab/.cache/huggingface \
    && chown -R lab:lab /workspace /home/lab/.cache

# Fail before a large dependency download if the publisher has not set the repo.
# Credentials must never be embedded in a Git URL, build ARG, or image layer.
RUN LAB_REPO_URL="${LAB_REPO_URL}" python -c \
    'import os, urllib.parse; u=os.environ["LAB_REPO_URL"]; p=urllib.parse.urlsplit(u); assert p.scheme=="https" and p.hostname and not p.username and not p.password and not p.query and not p.fragment and "REPLACE_" not in u, "Set the public HTTPS LAB_REPO_URL constant in docker-compose.yml before publishing this lab."'
RUN git clone --filter=blob:none "${LAB_REPO_URL}" /opt/lab/repository \
    && git -C /opt/lab/repository checkout --detach "${LAB_REPO_REF}" \
    && git -C /opt/lab/repository rev-parse HEAD > /opt/lab/repository-revision.txt \
    && test -f /opt/lab/repository/news_agent/Makefile \
    && test -f /opt/lab/repository/financials_agent/Makefile \
    && test -f /opt/lab/repository/orchestrator_agent/Makefile

COPY requirements.txt /opt/lab/requirements.txt
# Install CPU Torch first to avoid downloading an unused CUDA runtime on AMD64.
# The second install retains that satisfying Torch version. No version pins are relaxed.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --only-binary=:all: \
       --index-url https://download.pytorch.org/whl/cpu 'torch>=2.7,<3' \
    && python -m pip install --no-cache-dir -r /opt/lab/requirements.txt \
    && python -m pip check \
    && python -m pip freeze > /opt/lab/installed-requirements.txt

COPY validate_setup.py runtime_health.py container-entrypoint.sh /opt/lab/
# Normalize the entrypoint even when checked out by a Windows Git client.
RUN sed -i 's/\r$//' /opt/lab/container-entrypoint.sh \
    && chmod 755 /opt/lab/container-entrypoint.sh

USER lab
WORKDIR /workspace/agentic-rag
EXPOSE 8088 10000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=3).close()"
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/lab/container-entrypoint.sh"]
CMD ["python", "/opt/lab/runtime_health.py"]
