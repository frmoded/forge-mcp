# forge-mcp — production Dockerfile.
#
# Multi-stage build: builder installs the wheel into a venv, runtime
# copies the venv into a slim image and drops root privileges.
#
# Build:   docker build -t forge-mcp:latest .
# Run:     docker run --rm -p 8765:8765 \
#            -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
#            forge-mcp:latest
#
# The image does NOT bake FORGE_MCP_BEARER — auth extraction is per-
# request per CW-MCP-1-B. FORGE_MCP_BEARER as a container env var is a
# dev fallback ONLY (see server.py::_bearer_from_context); production
# clients pass Authorization on each request.

FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for building — none needed for pure-python dependencies,
# but keep the option for future wheels that need libc extras.
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

# Non-root runtime user.
RUN groupadd --system --gid 1001 forgemcp \
    && useradd --system --uid 1001 --gid 1001 --home /nonexistent \
        --shell /usr/sbin/nologin forgemcp

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Runtime config (all overridable at `docker run` time).
ENV FORGE_MCP_HOST=0.0.0.0
ENV FORGE_MCP_PORT=8765
ENV FORGE_TRANSPILE_URL=http://host.docker.internal:8000
ENV PYTHONUNBUFFERED=1

USER forgemcp

EXPOSE 8765

ENTRYPOINT ["python", "-m", "forge_mcp.server"]
