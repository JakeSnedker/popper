# Popper worker + dashboard image. One image, two roles (see docker-compose.yml).

# --- stage 1: build the dashboard UI FRESH so a stale gui/dist can never ship ---
# The repo's gui/dist may lag the source; building here means `docker build` always
# produces the current UI with no manual `npm run build` step to forget.
FROM node:20-slim AS gui
WORKDIR /gui
COPY gui/package*.json ./
RUN npm ci --no-fund --no-audit
COPY gui/ ./
RUN npm run build          # -> /gui/dist

# --- stage 2: python runtime ---
# Slim 3.12 base (matches requires-python >=3.11; project targets 3.12).
FROM python:3.12-slim

# Faster, quieter, no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the source. Data and secrets are excluded by .dockerignore and mounted at
# runtime instead (data = read-only volume, secrets = env_file). No secrets baked in.
COPY . /app
# Overlay the freshly-built UI over whatever gui/dist the context carried.
COPY --from=gui /gui/dist /app/gui/dist

# Editable install so popper stays at /app/popper: bars.py resolves the market data
# as parents[2]/data == /app/data, which is exactly where the volume mounts.
# Runtime deps (pandas/numpy/pyarrow/pyyaml) are now pinned in pyproject, so
# [supabase] (the shared-map store) is the only extra needed here.
RUN pip install -e ".[supabase]"

# Non-root. Own /app so the editable egg-info stays writable if pip re-touches it.
RUN useradd -m -u 10001 popper && chown -R popper:popper /app
USER popper

# Default role = the hunt worker loop. Overridden by the dashboard service.
# sh (not exec-bit) sidesteps CRLF/permission surprises when built on Windows.
CMD ["sh", "/app/docker-entrypoint.sh"]
