FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=5057

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
COPY data/questions.json ./data/questions.json
COPY data/demo_answers.json ./data/demo_answers.json
COPY railway_app.py ./railway_app.py
COPY railway-entrypoint.sh ./railway-entrypoint.sh
RUN chmod +x railway-entrypoint.sh

EXPOSE 5057
CMD ["./railway-entrypoint.sh"]
