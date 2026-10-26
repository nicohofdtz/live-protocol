# Server-Fassung im Container. Gebaut mit uv, gestartet ohne Build-Werkzeuge.
#
#   docker compose up        (liest die .env, siehe README)
#
# Daten (Einstellungen, Verbrauch, gespeicherte Sitzungen) liegen unter /data;
# compose.yaml hängt dort ein Volume ein.

FROM ghcr.io/astral-sh/uv:0.11-python3.14-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Erst nur die Abhängigkeiten: Diese Schicht bleibt im Cache, solange sich
# pyproject.toml und uv.lock nicht ändern.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY static ./static
COPY output_types ./output_types
COPY LICENSE THIRD_PARTY.md ./
COPY licenses ./licenses

FROM python:3.14-slim-trixie
WORKDIR /app
RUN useradd --create-home --uid 1000 live-protocol \
    && mkdir /data && chown live-protocol /data
COPY --from=build --chown=live-protocol /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DEPLOYMENT_MODE=hosted \
    XDG_DATA_HOME=/data \
    SESSIONS_DIR=/data/sessions \
    USER_PREFS_DIR=/data/prefs \
    USAGE_DIR=/data/usage \
    VAULT_DIR=/data/vault
USER live-protocol
VOLUME /data
EXPOSE 8000
# --proxy-headers: Hinter einem Reverse Proxy zählen dessen X-Forwarded-*.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
