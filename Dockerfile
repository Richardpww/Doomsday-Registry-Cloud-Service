# ---------- Stage 1: builder ----------
FROM python:3.12.7-slim-bookworm AS builder
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Dependency dulu, source belakangan -> layer cache terpakai
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12.7-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /opt/venv /opt/venv
COPY app/ .
USER appuser
EXPOSE 8000
CMD ["gunicorn", "--preload", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
