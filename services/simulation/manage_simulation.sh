#!/bin/bash

# Fleet Simulation Service Management Script
# Handles both MQTT Direct and FleetWise Edge (FWE) modes
# Usage: ./manage_simulation.sh [start|stop|status]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/simulation_api.pid"
API_URL="http://localhost:5001"
# DEPRECATED: custom GPS image (cms-fwe-gps) is no longer needed — GPS is now a CAN signal
FWE_IMAGE="public.ecr.aws/o0q5e8r2/cms-fwe-edge:v1.3.2-custom"
IOT_ENDPOINT=""
AWS_PROFILE_SELECTED=""

# ─── Helpers ───────────────────────────────────────────────────────────────────

info()  { echo "✅ $*"; }
warn()  { echo "⚠️  $*"; }
err()   { echo "❌ $*"; }
step()  { echo ""; echo "━━━ $* ━━━"; }

ask_yes_no() {
    local prompt="$1"
    read -p "$prompt [Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

select_profile() {
    local profiles
    profiles=$(aws configure list-profiles 2>/dev/null | sort)
    if [ -z "$profiles" ]; then
        err "No AWS profiles found. Run 'aws configure' first."
        exit 1
    fi
    echo "Available AWS profiles:"
    echo "$profiles" | nl -w2 -s') '
    echo ""
    read -p "Select profile number (or Enter for default): " num
    if [ -z "$num" ]; then
        AWS_PROFILE_SELECTED="default"
    else
        AWS_PROFILE_SELECTED=$(echo "$profiles" | sed -n "${num}p")
        [ -z "$AWS_PROFILE_SELECTED" ] && err "Invalid selection" && exit 1
    fi
    export AWS_PROFILE="$AWS_PROFILE_SELECTED"
    info "Using profile: $AWS_PROFILE_SELECTED"
}

# ─── Docker Runtime Detection ─────────────────────────────────────────────────

detect_docker_runtime() {
    # Returns "colima" or "docker-desktop"
    local name
    name=$(docker info --format '{{.Name}}' 2>/dev/null)
    if echo "$name" | grep -qi colima; then
        echo "colima"
    else
        echo "docker-desktop"
    fi
}

check_docker() {
    if command -v docker &>/dev/null && docker info &>/dev/null; then
        return 0
    fi
    return 1
}

fix_colima_dns() {
    colima ssh -- sudo sh -c '
        rm -f /etc/resolv.conf
        echo "nameserver 8.8.8.8" > /etc/resolv.conf
        echo "nameserver 8.8.4.4" >> /etc/resolv.conf
    ' 2>&1 | grep -v "unable to resolve host" || true
}

check_docker_network() {
    local runtime
    runtime=$(detect_docker_runtime)

    # Proactively fix DNS (Colima loses resolv.conf after sleep/wake)
    if [ "$runtime" = "colima" ]; then
        fix_colima_dns
    fi

    # Test connectivity to a known endpoint
    if ! docker run --rm --network host alpine sh -c "nc -zv -w3 8.8.8.8 53 2>&1" &>/dev/null; then
        if [ "$runtime" = "colima" ]; then
            warn "Docker container networking is broken (common after sleep/wake)"
            echo "  Restarting Colima to fix networking..."
            colima restart
            fix_colima_dns
            info "Colima restarted — networking restored"
        else
            warn "Docker container networking is broken — check Docker Desktop settings"
        fi
    fi
}

setup_docker_mac() {
    step "Setting up Docker via Colima"

    if ! command -v colima &>/dev/null; then
        echo "Installing Colima (lightweight Docker runtime for macOS)..."
        brew install colima docker
    fi

    if ! colima status &>/dev/null; then
        echo "Starting Colima with 6GB RAM, 4 CPUs (needed for vcan0 + FWE)..."
        colima start --cpu 4 --memory 6 --network-address
        # Fix DNS for AWS endpoints
        colima ssh -- sudo bash -c '
            mkdir -p /etc/systemd/resolved.conf.d
            echo -e "[Resolve]\nDNS=8.8.8.8\nFallbackDNS=8.8.4.4" > /etc/systemd/resolved.conf.d/dns.conf
            systemctl restart systemd-resolved
        '
        info "Colima started"
    else
        info "Colima already running"
    fi
}

setup_vcan() {
    local iface="${1:-vcan0}"
    step "Setting up virtual CAN bus ($iface)"

    # Check if already up
    if docker run --rm --network host --privileged alpine ip link show "$iface" 2>/dev/null | grep -q UP; then
        info "$iface already exists"
        return 0
    fi

    local runtime
    runtime=$(detect_docker_runtime)

    if [ "$runtime" = "colima" ]; then
        echo "Creating $iface via Colima VM..."
        docker run --rm --network host --privileged alpine sh -c "
            modprobe vcan 2>/dev/null || true
            ip link add dev $iface type vcan 2>/dev/null || true
            ip link set $iface up
        "
    else
        echo "Creating $iface via Docker Desktop (nsenter into VM)..."
        docker run --rm --network host --privileged --pid=host alpine \
            nsenter -t 1 -m -u -n -i -- sh -c "
                modprobe vcan 2>/dev/null || true
                ip link add dev $iface type vcan 2>/dev/null || true
                ip link set $iface up
            "
        if [ $? -ne 0 ]; then
            err "Docker Desktop kernel lacks vcan module."
            echo "  Switch to Colima: brew install colima && colima start --cpu 4 --memory 6 --network-address"
            return 1
        fi
    fi

    # Verify
    if docker run --rm --network host --privileged alpine ip link show "$iface" 2>/dev/null | grep -q UP; then
        info "$iface is up"
    else
        err "Failed to create $iface"
        return 1
    fi
}

setup_fwe_container() {
    local vehicle_id="$1"
    step "Setting up FleetWise Edge Agent for $vehicle_id"

    # Get IoT endpoint
    IOT_ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query 'endpointAddress' --output text)
    info "IoT endpoint: $IOT_ENDPOINT"

    # Get certificate from DDB
    echo "Fetching vehicle certificate..."
    local cert_json
    cert_json=$(python3 -c "
import boto3, json, sys
session = boto3.Session()
ddb = session.resource('dynamodb')
t = ddb.Table('cms-dev-storage-vehicle-certificates')

# Try direct lookup first
resp = t.get_item(Key={'vehicleId': '$vehicle_id'})
if 'Item' in resp:
    item = resp['Item']
else:
    # Fallback: scan by vin/thingName
    resp = t.scan(FilterExpression='vin = :v OR thingName = :v', ExpressionAttributeValues={':v': '$vehicle_id'}, Limit=1)
    if not resp.get('Items'):
        print('NOT_FOUND', file=sys.stderr)
        sys.exit(1)
    item = resp['Items'][0]

print(json.dumps({
    'cert': item['certificatePem'],
    'key': item['privateKey'],
    'thingName': item.get('thingName', item.get('vin', '$vehicle_id'))
}))
")

    if [ $? -ne 0 ]; then
        err "No certificate found for $vehicle_id. Create the vehicle in the UI first."
        exit 1
    fi

    local thing_name cert_pem private_key
    thing_name=$(echo "$cert_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['thingName'])")
    cert_pem=$(echo "$cert_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['cert'])")
    private_key=$(echo "$cert_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])")

    info "Thing name: $thing_name"

    # Pull FWE image if needed
    if ! docker image inspect "$FWE_IMAGE" &>/dev/null; then
        echo "Pulling FWE image (first time only)..."
        docker pull "$FWE_IMAGE"
    fi

    # Generate persistency files
    echo "Generating FWE decoder manifest and collection scheme..."
    cd "$SCRIPT_DIR"
    if [ -d "can_env" ]; then
        can_env/bin/python3 generate_fwe_persistency.py --profile "$AWS_PROFILE_SELECTED" --region us-east-1
    else
        python3 generate_fwe_persistency.py --profile "$AWS_PROFILE_SELECTED" --region us-east-1
    fi

    # Stop existing FWE container
    docker stop cms-fwe-agent 2>/dev/null || true
    docker rm -f cms-fwe-agent 2>/dev/null || true

    # Start FWE
    echo "Starting FWE container..."
    docker run --rm -d --name cms-fwe-agent --network host --privileged \
        -v /tmp/fwe_e2e_certs:/var/aws-iot-fleetwise/ \
        "$FWE_IMAGE" \
        --vehicle-name "$thing_name" \
        --endpoint-url "$IOT_ENDPOINT" \
        --iotfleetwise-topic-prefix "cms/fleetwise/" \
        --can-bus0 vcan0 \
        --certificate "$cert_pem" \
        --private-key "$private_key" \
        --log-level Info \
        --persistency-path /var/aws-iot-fleetwise/

    # Wait for connection
    echo "Waiting for FWE to connect..."
    for i in $(seq 1 15); do
        sleep 2
        if docker logs cms-fwe-agent 2>&1 | grep -q "Engine Connected"; then
            info "FWE connected to IoT Core"
            return 0
        fi
    done
    warn "FWE started but connection not confirmed yet. Check: docker logs cms-fwe-agent"
}

# ─── Commands ─────────────────────────────────────────────────────────────────

cmd_start() {
    echo "🚀 Fleet Simulation Service"
    echo "==========================="
    echo ""

    # Check if already running
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        warn "Service already running (PID: $(cat "$PID_FILE"))"
        echo "API: $API_URL"
        exit 0
    fi

    # Select AWS profile
    select_profile

    # Check if Docker/FWE infrastructure is available, set it up if not
    if check_docker; then
        step "Docker available — setting up FWE infrastructure"
    else
        echo ""
        echo "Docker is not running. FWE mode requires Docker for virtual CAN bus."
        if [[ "$(uname)" == "Darwin" ]]; then
            if ask_yes_no "Install Docker via Colima?"; then
                setup_docker_mac
            else
                info "Skipping Docker setup. MQTT Direct mode will still work."
            fi
        fi
    fi

    # Set up FWE infrastructure if Docker is available
    if check_docker; then
        local runtime
        runtime=$(detect_docker_runtime)
        info "Docker runtime: $runtime"

        check_docker_network
        setup_vcan vcan0

        step "Pulling FleetWise Edge Agent image"
        if docker image inspect "$FWE_IMAGE" &>/dev/null; then
            info "FWE image already available"
        else
            echo "Authenticating with ECR Public..."
            aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws 2>/dev/null || true
            echo "Downloading FWE image (first time only, ~88MB)..."
            docker pull "$FWE_IMAGE"
            info "FWE image pulled"
        fi

        if [ "$runtime" = "colima" ]; then
            fix_colima_dns
        fi

        info "FWE infrastructure ready. Both MQTT Direct and FleetWise Edge modes available in the UI."
    else
        info "No Docker — only MQTT Direct mode available in the UI."
    fi

    # Start the simulation API service
    step "Starting Simulation API Service"
    cd "$SCRIPT_DIR"
    nohup ./start_simulation_service.sh > simulation_service.log 2>&1 &
    echo $! > "$PID_FILE"

    sleep 3
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        info "Simulation service started (PID: $(cat "$PID_FILE"))"
        echo ""
        echo "🌐 API: $API_URL"
        echo "📋 Logs: tail -f $SCRIPT_DIR/simulation_service.log"
        if check_docker; then
            echo "🔧 FWE logs: docker logs -f cms-fwe-<vin>"
            echo ""
            echo "Both modes available in the UI — select MQTT Direct or FleetWise Edge when starting a simulation."
        fi
    else
        err "Service failed to start. Check: cat simulation_service.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

cmd_stop() {
    echo "🛑 Stopping Fleet Simulation Service..."

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null && info "Service stopped (PID: $PID)" || warn "Service was not running"
        rm -f "$PID_FILE"
    fi

    # Kill anything on port 5001
    lsof -ti:5001 2>/dev/null | xargs kill -9 2>/dev/null || true
    pkill -f "simulation_api" 2>/dev/null || true

    # Stop FWE container if running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q cms-fwe-agent; then
        docker stop cms-fwe-agent 2>/dev/null
        info "FWE container stopped"
    fi

    info "Done"
}

cmd_status() {
    echo "📊 Fleet Simulation Service Status"
    echo "==================================="

    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        info "Simulation service running (PID: $(cat "$PID_FILE"))"
        curl -s "$API_URL/api/simulation/presets" >/dev/null && info "API responding" || warn "API not responding"
    else
        echo "   Simulation service: not running"
    fi

    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q cms-fwe-agent; then
        info "FWE container running"
        docker logs cms-fwe-agent 2>&1 | grep "Engine Connected" | tail -1 | sed 's/^/   /'
    else
        echo "   FWE container: not running"
    fi

    if docker run --rm --network host --privileged alpine ip link show vcan0 &>/dev/null 2>&1; then
        info "vcan0 interface up"
    else
        echo "   vcan0: not available"
    fi
}

# ─── Main ─────────────────────────────────────────────────────────────────────

case "${1:-}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)
        echo "Fleet Simulation Service"
        echo "========================"
        echo ""
        echo "Usage: $0 [start|stop|status]"
        echo ""
        echo "  start   Start service (prompts for mode: MQTT Direct or FleetWise Edge)"
        echo "  stop    Stop service and FWE container"
        echo "  status  Check service, FWE, and vcan0 status"
        ;;
esac
