# syntax=docker/dockerfile:1.7
# Lightsmith Materials API image (also runs the migrate and import jobs).
#   docker build -t lightsmith-materials .
# Only the official python image is used. Dependencies come from requirements.lock, a hash-pinned export
# of uv.lock (regenerate with: uv export --frozen --no-dev --no-emit-project -o requirements.lock).
# Optional: bundle the simulation library to enable /model fits, e.g.
#   docker build --build-arg LIBRARY_SPEC="lightsmith==0.2.0" -t lightsmith-materials .
ARG PYTHON_IMAGE=python:3.12-slim

# --- build: install the locked dependencies and the service into /opt/venv ---------------
FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
RUN python -m venv /opt/venv
WORKDIR /src
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --require-hashes --no-deps -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip /opt/venv/bin/pip install --no-deps .
ARG LIBRARY_SPEC=""
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ -n "$LIBRARY_SPEC" ]; then /opt/venv/bin/pip install "$LIBRARY_SPEC"; fi

# --- runtime: no build tools, non-root ---------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data && chown app:app /app /data
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MATERIALS_DB_DATA_DIR=/data HOST=0.0.0.0 PORT=8100
WORKDIR /app
USER app:app
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4)"]
CMD ["materials-db", "serve"]
