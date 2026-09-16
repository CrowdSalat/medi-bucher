# ── stage 1: builder ──────────────────────────────────────────────
FROM docker.io/library/python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY booker/ booker/

RUN pip install --no-cache-dir --prefix=/install .

# ── stage 2: runtime ─────────────────────────────────────────────
FROM docker.io/library/python:3.12-slim AS runtime

# Line-buffer stdout even when piped (kubelet/"oc logs"): Python buffers
# stdout when it's not a tty, so without this the daemon logs appear empty.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=builder /install /usr/local

RUN mkdir -p /app && chmod 775 /app && chgrp 0 /app

USER 65534:0

ENTRYPOINT ["python3", "-m", "booker"]
CMD ["config.yaml"]
