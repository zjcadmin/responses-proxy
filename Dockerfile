FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESPONSES_PROXY_DATA_DIR=/data \
    RESPONSES_PROXY_MANAGER_HOST=0.0.0.0 \
    RESPONSES_PROXY_MANAGER_PORT=8899

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY *.example.json ./

RUN pip install --no-cache-dir .

RUN mkdir -p /data/runtime

EXPOSE 8899 8800

CMD ["python", "scripts/run_manager.py"]
