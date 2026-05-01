FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir . mcpo

# SSH client for host tools
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*

# Add passwd entry for uid 1000 so SSH works (container runs as non-root uid 1000)
RUN echo "mcp:x:1000:1000:mcp:/tmp:/bin/sh" >> /etc/passwd && \
    mkdir -p /tmp/.ssh && chmod 700 /tmp/.ssh

EXPOSE 8080

# mcpo wraps the stdio MCP server (homelab-mcp) as an OpenAPI HTTP bridge.
# Without mcpo, homelab-mcp reads stdin and exits immediately in a pod.
ENTRYPOINT ["mcpo", "--port", "8080", "--host", "0.0.0.0", "--", "homelab-mcp"]
