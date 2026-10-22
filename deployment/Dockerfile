# Curation workbench: one service, the API + SPA (`cwb hosted serve`, port
# 8134). It reads each project's committed snapshot file from GitHub over
# HTTPS and never runs project code. The swarm stack file and deploy script
# live in cloud-management/gcp/compute (`deploy.sh curate`); deployment/deploy.md
# is the runbook.
#
# SINGLE-REPO build (snapgate is vendored under src/snapgate):
#
#   docker build -t curate:dev -f deployment/Dockerfile .

# Base images: a deploy script may pass digest-pinned values; these defaults are for local builds.
ARG NODE_BASE=node:22-slim
ARG PYTHON_BASE=python:3.14-slim

# --- stage 1: build the SPA (writes /build/src/curation_workbench/web) ---
FROM ${NODE_BASE} AS spa
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm --prefix frontend ci
COPY frontend/ frontend/
RUN npm --prefix frontend run build && test -f /build/src/curation_workbench/web/index.html

# --- stage 2: runtime ---
FROM ${PYTHON_BASE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv makes the venv and installs (an exact uv tag, never latest).
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH \
    UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN uv venv /opt/venv

WORKDIR /app
# Dependencies install in their own layer ABOVE the source copy, so a
# code-only change reuses the cached deps layer. The stub packages exist only
# so hatchling can build the wheel (it requires both listed package dirs; the
# `artifacts` entry for the SPA tolerates a missing folder); the stub is
# uninstalled, then the real source installs with --no-deps.
COPY pyproject.toml ./
RUN mkdir -p src/curation_workbench src/snapgate \
 && touch src/curation_workbench/__init__.py src/snapgate/__init__.py \
 && uv pip install ".[hosted]" && uv pip uninstall curation-workbench && rm -rf src
COPY src ./src
# The SPA ships as package data inside the installed package.
COPY --from=spa /build/src/curation_workbench/web src/curation_workbench/web
RUN uv pip install --no-deps .
# Tripwire: fail the BUILD if the SPA did not land in the installed package.
RUN python -c "import curation_workbench, pathlib, sys; p = pathlib.Path(curation_workbench.__file__).parent / 'web' / 'index.html'; sys.exit(0 if p.is_file() else 'SPA missing from the installed package')"

# The numbered migrations MUST be in the image (`cwb db migrate` runs before
# serving). The runner refuses a missing anchor; this tripwire fails the BUILD first.
COPY deployment/migrations /app/deployment/migrations
ENV CWB_MIGRATIONS_DIR=/app/deployment/migrations
RUN ls /app/deployment/migrations/010_projects.sql /app/deployment/migrations/001_snapgate.sql >/dev/null

# One healthcheck definition, shared with the stack file's healthcheck: 200 (ok)
# and 503 (degraded) both mean alive, so a failing check never crash-loops the
# only replica.
COPY deployment/healthcheck.py /app/deployment/healthcheck.py

# Unprivileged runtime user (uid 1000:1000). No setuid/setgid programs: Swarm
# cannot set no-new-privileges, so nothing in the image may raise privileges.
RUN groupadd -g 1000 cwb && useradd -u 1000 -g 1000 -m -d /home/cwb -s /usr/sbin/nologin cwb \
 && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
 && test -z "$(find / -xdev -type f -perm /6000)" \
 && test "$(id -g cwb)" = 1000
ENV HOME=/home/cwb
USER cwb

EXPOSE 8134

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
    CMD ["python", "/app/deployment/healthcheck.py"]

CMD ["cwb", "hosted", "serve", "--host", "0.0.0.0", "--port", "8134"]
