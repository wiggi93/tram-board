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
RUN if [ "$WITH_LED" = "1" ]; then \
      apt-get update && \
      apt-get install -y --no-install-recommends git build-essential && \
      pip install --no-cache-dir Cython && \
      git clone --depth=1 https://github.com/hzeller/rpi-rgb-led-matrix /tmp/matrix && \
      cd /tmp/matrix && \
      make build-python PYTHON=$(which python3) && \
      make install-python PYTHON=$(which python3) && \
      cd / && rm -rf /tmp/matrix && \
      pip uninstall -y Cython && \
      apt-get purge -y git build-essential && \
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
