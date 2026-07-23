FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . .

RUN pip install --upgrade pip && \
    pip install .

RUN chmod +x /app/scripts/*.sh

EXPOSE 8000

CMD ["bash", "/app/scripts/start-web.sh"]
