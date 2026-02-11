#!/bin/bash
# Wrapper script to ensure Piper TTS Docker container is running
# and then start the MCP server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="piper"
IMAGE_NAME="piper-tts-mcp:latest"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    # Check if container exists but stopped
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker start "$CONTAINER_NAME" >/dev/null 2>&1
    else
        # Start new container
        docker run -d --name "$CONTAINER_NAME" -p 5001:5000 --restart unless-stopped "$IMAGE_NAME" >/dev/null 2>&1
    fi

    # Wait for service to be ready
    for i in {1..30}; do
        if curl -s -o /dev/null -w "%{http_code}" http://localhost:5001 2>/dev/null | grep -q "405\|200"; then
            break
        fi
        sleep 0.5
    done
fi

# Ensure asdf shims are on PATH (needed when launched by daemons like MCPProxy)
if [ -d "$HOME/.asdf/shims" ]; then
    export PATH="$HOME/.asdf/shims:$PATH"
fi

# Run the MCP server
cd "$SCRIPT_DIR"
exec uv run server.py
