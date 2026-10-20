# Vigie API image: a small FastAPI service, run as a non-root user.
#
#   docker build -t vigie-api .
#   docker run --rm -p 8080:8080 -v vigie-data:/data -e VIGIE_CONTACT=you@example.org vigie-api
#
# The settings are the VIGIE_* environment variables of the README. The database lives in /data.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="Vigie API" \
      org.opencontainers.image.description="World hazards and weather for the Vigie displays (FastAPI). A personal project, shared in case it helps." \
      org.opencontainers.image.source="https://github.com/Axellum/vigie-api" \
      org.opencontainers.image.licenses="MIT"

# RainViewer's free API is for personal and educational use only: off in the image, on again with
# -e VIGIE_ENABLE_RAINVIEWER=1 for a personal instance (see SOURCES.md).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIGIE_DB_PATH=/data/vigie.db \
    VIGIE_ENABLE_RAINVIEWER=0

WORKDIR /app

# Runtime dependencies, read from pyproject.toml (the one list of them), in a layer of their own.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install --root-user-action=ignore -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY LICENSE ./
COPY app ./app

# A user with no shell and no home; /data is the only place it can write.
RUN useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin vigie \
 && mkdir /data \
 && chown vigie:vigie /data
USER vigie
VOLUME /data

EXPOSE 8080

# /v1/health answers without calling any provider.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=3s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
