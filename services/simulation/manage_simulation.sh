#!/bin/bash

# Fleet Simulation Service Management Script
# Usage: ./manage_simulation.sh [start|stop|status|test-safety]

WORKSPACE_ROOT="/Users/givenand/connected-mobility-workspace"
SIMULATION_DIR="$WORKSPACE_ROOT/services/simulation"
PID_FILE="$SIMULATION_DIR/simulation_api.pid"
API_URL="http://localhost:5001"

case "$1" in
    start)
        echo "🚀 Starting Fleet Simulation Service..."
        cd "$SIMULATION_DIR"
        
        # Check if already running
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "⚠️  Service is already running (PID: $(cat $PID_FILE))"
            exit 1
        fi
        
        # Get available AWS profiles
        profiles=$(aws configure list-profiles 2>/dev/null | sort)
        if [ -z "$profiles" ]; then
            echo "❌ No AWS profiles found. Please configure AWS credentials."
            exit 1
        fi
        
        echo "Available AWS profiles:"
        echo "$profiles" | nl -w2 -s') '
        echo ""
        read -p "Select profile number (or press Enter for default): " profile_num
        
        if [ -z "$profile_num" ]; then
            selected_profile="default"
        else
            selected_profile=$(echo "$profiles" | sed -n "${profile_num}p")
            if [ -z "$selected_profile" ]; then
                echo "❌ Invalid profile number"
                exit 1
            fi
        fi
        
        echo "✅ Using profile: $selected_profile"
        
        # Start the service in background with selected AWS profile
        export AWS_PROFILE="$selected_profile"
        nohup ./start_simulation_service.sh > simulation_service.log 2>&1 &
        echo $! > "$PID_FILE"
        
        echo "✅ Service started (PID: $(cat $PID_FILE))"
        echo "📋 Log file: $SIMULATION_DIR/simulation_service.log"
        echo "🌐 API available at: $API_URL"
        
        # Wait a moment and check if it's running
        sleep 3
        if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "🎉 Service is running successfully!"
        else
            echo "❌ Service failed to start. Check the log file."
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;
        
    stop)
        echo "🛑 Stopping Fleet Simulation Service..."
        
        # Kill process from PID file
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                kill "$PID"
                echo "✅ Service stopped (PID: $PID)"
                rm -f "$PID_FILE"
            else
                echo "⚠️  Service was not running"
                rm -f "$PID_FILE"
            fi
        else
            echo "⚠️  PID file not found. Service may not be running."
        fi
        
        # Kill any remaining processes using port 5001
        PORT_PIDS=$(lsof -ti:5001 2>/dev/null)
        if [ -n "$PORT_PIDS" ]; then
            echo "🔄 Killing processes using port 5001..."
            echo "$PORT_PIDS" | xargs kill -9 2>/dev/null
        fi
        
        # Kill any simulation_api processes
        pkill -f "simulation_api" 2>/dev/null && echo "🔄 Killed remaining simulation processes"
        
        # Wait a moment for cleanup
        sleep 2
        ;;
        
    status)
        echo "📊 Fleet Simulation Service Status"
        echo "=================================="
        
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            PID=$(cat "$PID_FILE")
            echo "✅ Service is running (PID: $PID)"
            echo "🌐 API URL: $API_URL"
            
            # Test API connectivity
            if curl -s "$API_URL/api/simulation/presets" > /dev/null; then
                echo "🔗 API is responding"
            else
                echo "❌ API is not responding"
            fi
        else
            echo "❌ Service is not running"
            [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
        fi
        ;;
        
    test-safety)
        echo "🧪 Testing Safety Events Simulation"
        echo "===================================="
        
        # Check if service is running
        if ! curl -s "$API_URL/api/simulation/presets" > /dev/null; then
            echo "❌ Simulation service is not running. Start it first with:"
            echo "   ./manage_simulation.sh start"
            exit 1
        fi
        
        echo "🎯 Starting Safety Event Focus simulation..."
        echo "   Duration: 15 minutes"
        echo "   Vehicles: 8"
        echo "   Safety Rate: 35% (high for testing)"
        echo "   Fleet Prefix: SAFE"
        echo ""
        
        # Start safety-focused simulation
        RESPONSE=$(curl -s -X POST "$API_URL/api/simulation/start" \
            -H "Content-Type: application/json" \
            -d '{
                "duration": 15,
                "vehicles": 8,
                "city": "seattle",
                "safety_rate": 0.35,
                "interval": 20,
                "fleet_prefix": "SAFE",
                "cleanup": true
            }')
        
        if echo "$RESPONSE" | grep -q "error"; then
            echo "❌ Failed to start simulation:"
            echo "$RESPONSE"
            exit 1
        else
            echo "✅ Safety simulation started successfully!"
            echo "📊 Response: $RESPONSE"
            echo ""
            echo "🔍 Monitor the simulation:"
            echo "   • Check CMS UI Safety Alerts dashboard"
            echo "   • Watch for safety events in real-time data"
            echo "   • Events should appear within 1-2 minutes"
            echo ""
            echo "📋 View active simulations:"
            echo "   curl $API_URL/api/simulation/list"
        fi
        ;;
        
    *)
        echo "Fleet Simulation Service Management"
        echo "=================================="
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  start       Start the simulation service"
        echo "  stop        Stop the simulation service"
        echo "  status      Check service status"
        echo "  test-safety Start a safety-focused simulation for testing"
        echo ""
        echo "Examples:"
        echo "  $0 start                # Start the service"
        echo "  $0 test-safety         # Run safety events simulation"
        echo "  $0 status              # Check if running"
        echo "  $0 stop                # Stop the service"
        ;;
esac
