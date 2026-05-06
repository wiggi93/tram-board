FROM python:3.11-slim AS builder

ARG WITH_LED=0

# Pin to the last upstream commit before scikit-build-core / Pillow shim was
# added (Feb 2026 refactor needs Pillow private headers).
ARG RGBMATRIX_REF=02fb09a6099cd0aa1eb44b9b663cdc2af9b8cda3

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends git build-essential python3-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir Cython

# Always materialise the output dir so the COPY in the runtime stage is
# unconditional. Compile rgbmatrix into it only when WITH_LED=1.
RUN mkdir -p /build/out && \
    if [ "$WITH_LED" = "1" ]; then \
      git clone https://github.com/hzeller/rpi-rgb-led-matrix.git /tmp/matrix && \
      cd /tmp/matrix && git checkout "$RGBMATRIX_REF" && \
      make build-python PYTHON=$(which python3) && \
      cp -r /tmp/matrix/bindings/python/. /build/out/; \
    fi

# ============================================
# Runtime Stage
# ============================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAM_BOARD_CONFIG=/app/data/config.json \
    PYTHONPATH=/opt/rgbmatrix

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /build/out /opt/rgbmatrix

COPY app.py config.py tram.py led.py ./
COPY templates ./templates
COPY fonts ./fonts

RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 8080

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]
