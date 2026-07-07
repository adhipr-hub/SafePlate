# SafePlate — containerized for cloud deployment (AWS App Runner / ECS, Render, etc.)
#
# A container is a sealed box holding the app + the exact Python and libraries it
# needs, so it runs identically on your laptop and in the cloud. Build it with:
#   docker build -t safeplate .
# Run it locally with:
#   docker run --rm -p 8765:8765 safeplate
# then open http://127.0.0.1:8765

# Start from a slim image that already has Python 3.12. (Pinned for reproducible,
# wheel-only installs; bump to 3.13/3.14 once you've confirmed the deps build.)
FROM python:3.12-slim

# Runtime environment:
# - PYTHONUNBUFFERED so logs stream out immediately (important in the cloud).
# - PYTHONDONTWRITEBYTECODE keeps .pyc clutter out of the image.
# - SAFEPLATE_HOST=0.0.0.0 binds all interfaces; the app's default 127.0.0.1
#   would only be reachable from *inside* the container, so the cloud proxy
#   could never reach it.
# - PORT is honoured by start_safeplate_app.py; the platform overrides it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SAFEPLATE_HOST=0.0.0.0 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PORT=8765

WORKDIR /app

# Install dependencies in their own layer FIRST, so editing source code doesn't
# force pip to reinstall everything on every rebuild (Docker caches this layer).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium (+ its system libraries) for JS-rendered menus in the Deep-Dive
# Dossier. Installed to PLAYWRIGHT_BROWSERS_PATH so the non-root runtime user
# can read it. If this layer is removed, the app still runs -- the dossier just
# degrades to static fetching.
RUN playwright install --with-deps chromium

# Then copy the application code.
COPY . .

# Run as a non-root user (security best practice; some platforms require it).
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Documentation only; the real listening port comes from $PORT at runtime.
EXPOSE 8765

# Liveness probe Docker / the platform can use. /healthz is exempt from auth, so
# this works even when SAFEPLATE_PASSWORD is set.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8765'))" || exit 1

# Launch the server. --no-browser because there's no browser inside a container.
CMD ["python", "scripts/start_safeplate_app.py", "--no-browser"]
