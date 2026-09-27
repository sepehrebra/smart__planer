FROM node:24-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY db ./db
COPY scripts ./scripts
COPY --from=frontend /web/dist /app/frontend/dist
RUN python -m pip install --no-cache-dir .
USER 10001:10001
EXPOSE 8000
CMD ["python", "scripts/start_dev.py"]
