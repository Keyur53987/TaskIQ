FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home taskiq
COPY --chown=taskiq:taskiq . .
USER taskiq
EXPOSE 8000
# Run migrations separately as a release step, never once per web worker.
CMD ["python", "scripts/render_start.py"]
