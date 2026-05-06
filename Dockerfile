FROM python:3.11-slim as builder

ARG WITH_LED=0

WORKDIR /build

# Install build dependencies only in builder stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    build-essential \
    python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python build dependencies
RUN pip install --no-cache-dir Cython

# Conditionally build rpi-rgb-led-matrix
RUN if [ "$WITH_LED" = "1" ]; then \
      git clone https://github.com/hzeller/rpi-rgb-led-matrix.git /build/rpi-rgb-led-matrix && \
      cd /build/rpi-rgb-led-matrix && \
      git checkout v1.32 && \
      make build-python PYTHON=$(which python3) && \
      make install-python PYTHON=$(which python3); \
    fi

# ============================================
# Runtime Stage
# ============================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAM_BOARD_CONFIG=/app/data/config.json

WORKDIR /app

# Copy Python requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py config.py tram.py led.py ./
COPY templates ./templates
COPY fonts ./fonts

# Copy compiled rgbmatrix bindings if LED support was built
ARG WITH_LED=0
COPY --from=builder --chown=root:root /build/rpi-rgb-led-matrix/bindings/python /opt/rpi-rgb-led-matrix/bindings/python 2>/dev/null || true

# Set PYTHONPATH for rgbmatrix if available
RUN if [ "$WITH_LED" = "1" ]; then \
      echo 'export PYTHONPATH="/opt/rpi-rgb-led-matrix/bindings/python:$PYTHONPATH"' >> /etc/profile.d/rgbmatrix.sh; \
    fi

# Create data directory for volumes
RUN mkdir -p /app/data

VOLUME ["/app/data"]

EXPOSE 8080

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]
