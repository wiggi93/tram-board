FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAM_BOARD_CONFIG=/app/data/config.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py tram.py ./
COPY templates ./templates
COPY static ./static
COPY fonts ./fonts

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8080

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]
