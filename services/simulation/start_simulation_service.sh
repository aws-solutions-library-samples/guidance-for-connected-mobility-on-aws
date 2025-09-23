#!/bin/bash

# Fleet Simulation Service Startup Script for CMS Workspace
# This script starts the simulation API server within the CMS workspace environment

set -e  # Exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../../" && pwd)"
SIMULATION_DIR="$SCRIPT_DIR"
VENV_PATH="$WORKSPACE_ROOT/lib/.venv"

echo "🚀 Starting Fleet Simulation Service"
echo "====================================="
echo "📁 Workspace: $WORKSPACE_ROOT"
echo "🔧 Simulation Service: $SIMULATION_DIR"
echo "🐍 Virtual Environment: $VENV_PATH"
echo ""

# Change to simulation directory
cd "$SIMULATION_DIR"

# Ensure we're using Node.js 22 for consistency
if command -v nvm &> /dev/null; then
    echo "🔄 Setting Node.js to v22 for consistency..."
    source ~/.nvm/nvm.sh && nvm use 22 2>/dev/null || echo "   Node.js 22 not available, using current version"
fi

# Activate virtual environment if it exists
if [ -d "$VENV_PATH" ]; then
    echo "🔄 Activating virtual environment..."
    source "$VENV_PATH/bin/activate"
    echo "✅ Virtual environment activated"
else
    echo "⚠️  Virtual environment not found at $VENV_PATH"
    echo "   Using system Python..."
fi

# Check and install dependencies
echo ""
echo "📦 Checking Python dependencies..."

# Function to install dependencies
install_deps() {
    echo "⚠️  Installing missing dependencies..."
    
    if [ -d "$VENV_PATH" ]; then
        # Install in virtual environment
        pip install flask flask-cors boto3 requests
    else
        # Try different installation methods for system Python
        if command -v pip &> /dev/null; then
            pip install --user flask flask-cors boto3 requests
        elif python3 -m pip --version &> /dev/null; then
            python3 -m pip install --break-system-packages flask flask-cors boto3 requests 2>/dev/null || \
            python3 -m pip install --user flask flask-cors boto3 requests
        else
            echo "❌ Could not find pip. Please install dependencies manually:"
            echo "   python3 -m pip install flask flask-cors boto3 requests"
            exit 1
        fi
    fi
}

# Check if required packages are available
missing_deps=()
for package in flask flask_cors boto3 requests; do
    if ! python3 -c "import $package" 2>/dev/null; then
        missing_deps+=($package)
    fi
done

if [ ${#missing_deps[@]} -gt 0 ]; then
    echo "❌ Missing dependencies: ${missing_deps[*]}"
    install_deps
else
    echo "✅ All dependencies are available"
fi

# Check if simulation script exists
SIMULATION_SCRIPT="$SCRIPT_DIR/run_fleet_simulation.py"
if [ ! -f "$SIMULATION_SCRIPT" ]; then
    echo ""
    echo "⚠️  Main simulation script not found at: $SIMULATION_SCRIPT"
    echo "   The API will start but simulations may fail."
    echo "   Please ensure the simulation script is available."
fi

# Start the API server
echo ""
echo "🌐 Starting Flask API server on http://localhost:5001"
echo "📊 API endpoints:"
echo "   GET  /api/simulation/presets     - Get simulation presets"
echo "   POST /api/simulation/start       - Start a simulation"
echo "   GET  /api/simulation/list        - List active simulations"
echo "   POST /api/simulation/stop/{id}   - Stop a simulation"
echo ""
echo "🔗 Frontend integration: The CMS UI will connect to this API"
echo "🛑 Press Ctrl+C to stop the server"
echo ""

# Set environment variables
export FLASK_ENV=development
export FLASK_DEBUG=1

# Start the Flask application
python3 simulation_api.py
