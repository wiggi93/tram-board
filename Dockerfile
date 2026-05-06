FROM python:3.11-slim

ARG WITH_LED=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAM_BOARD_CONFIG=/app/data/config.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optionally compile rpi-rgb-led-matrix (Raspberry Pi only). Build deps are
# purged afterwards to keep the image small.
# Pin to a known-good upstream commit. After Feb 2026 the project switched
# to a scikit-build-core build that depends on Pillow's private Imaging.h
# which isn't installed by Pillow wheels — pre-refactor it builds cleanly.
ARG RGBMATRIX_REF=02fb09a6099cd0aa1eb44b9b663cdc2af9b8cda3

RUN if [ "$WITH_LED" = "1" ]; then \
      apt-get update && \
      apt-get install -y --no-install-recommends git build-essential python3-dev && \
      pip install --no-cache-dir Cython && \
      git clone https://github.com/hzeller/rpi-rgb-led-matrix /tmp/matrix && \
      cd /tmp/matrix && git checkout "$RGBMATRIX_REF" && \
      make build-python PYTHON=$(which python3) && \
      make install-python PYTHON=$(which python3) && \
      cd / && rm -rf /tmp/matrix && \
      pip uninstall -y Cython && \
      apt-get purge -y git build-essential python3-dev && \
      apt-get autoremove -y && \
      apt-get clean && \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY app.py config.py tram.py led.py ./
COPY templates ./templates
COPY fonts ./fonts

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8080

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]
