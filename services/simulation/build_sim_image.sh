#!/bin/bash
# Build the pre-baked simulator Docker image (requires internet access)
# This eliminates the 2-3 minute apt-get/pip install on every simulation run.
# Usage: ./build_sim_image.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Building cms-sim-local image..."
docker build -t cms-sim-local -f "$SCRIPT_DIR/Dockerfile.sim" "$SCRIPT_DIR"
echo "✅ Image built. Simulations will now start instantly."
