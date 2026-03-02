#!/usr/bin/env python3
"""
Fleet Simulation API Endpoint
Provides REST API to control fleet simulations from the CMS UI
"""

import json
import os
import sys
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend requests

# AWS Profile configuration
AWS_PROFILE = None

@app.route('/api/simulation/drivers', methods=['GET'])
def get_available_drivers():
    """Get list of available drivers for simulation"""
    try:
        import boto3
        
        # Try to get drivers from database
        try:
            # Try default profile first
            dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            drivers_table = dynamodb.Table(f'cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-drivers')
            
            response = drivers_table.scan(
                FilterExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'}
            )
            
            drivers = response.get('Items', [])
            
            # Format drivers for UI
            formatted_drivers = []
            for driver in drivers:
                formatted_drivers.append({
                    'driverId': driver['driverId'],
                    'name': f"{driver.get('firstName', '')} {driver.get('lastName', '')}".strip(),
                    'email': driver.get('email', ''),
                    'status': driver.get('status', 'active')
                })
            
            return jsonify({
                'success': True,
                'drivers': formatted_drivers,
                'count': len(formatted_drivers)
            })
            
        except Exception as e:
            print(f"Error loading drivers: {e}")
            return jsonify({
                'success': False,
                'error': 'Could not load drivers from database',
                'drivers': [],
                'count': 0
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def select_aws_profile():
    """Select AWS profile on startup"""
    global AWS_PROFILE
    
    try:
        # Check if AWS_PROFILE is set as environment variable
        env_profile = os.environ.get('AWS_PROFILE')
        if env_profile:
            print(f"🔧 Using AWS profile from environment: {env_profile}")
            AWS_PROFILE = env_profile
            
            # Validate the profile works
            import boto3
            session = boto3.Session(profile_name=AWS_PROFILE)
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            
            print(f"✅ AWS Profile configured successfully!")
            print(f"   Profile: {AWS_PROFILE}")
            print(f"   Account: {identity['Account']}")
            print(f"   User: {identity.get('Arn', 'Unknown')}")
            
            return True
        
        # Get available profiles
        import boto3
        session = boto3.Session()
        available_profiles = session.available_profiles
        
        if not available_profiles:
            print("❌ No AWS profiles found. Please configure AWS credentials.")
            return False
        
        # Use target-account if available, otherwise use first available
        if 'target-account' in available_profiles:
            AWS_PROFILE = 'target-account'
            print(f"🔧 Using target-account AWS profile")
        elif 'default' in available_profiles:
            AWS_PROFILE = 'default'
            print(f"🔧 Using default AWS profile")
        else:
            AWS_PROFILE = available_profiles[0]
            print(f"🔧 Using first available AWS profile: {AWS_PROFILE}")
        
        # Validate the profile works
        session = boto3.Session(profile_name=AWS_PROFILE)
        sts = session.client('sts')
        identity = sts.get_caller_identity()
        
        print(f"✅ AWS Profile configured successfully!")
        print(f"   Profile: {AWS_PROFILE}")
        print(f"   Account: {identity['Account']}")
        print(f"   User: {identity.get('Arn', 'Unknown')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error configuring AWS profile: {e}")
        return False

# Global simulation tracking
active_simulations = {}
simulation_history = []

class SimulationManager:
    """Manages simulation processes"""
    
    def __init__(self):
        self.simulations = {}
        
    def start_simulation(self, config: Dict[str, Any]) -> str:
        """Start a new simulation with account validation"""
        simulation_id = str(uuid.uuid4())[:8]
        
        # Sanitize string inputs to prevent command injection
        def sanitize_string(value: str, allowed_chars: str = 'a-zA-Z0-9_-') -> str:
            """Remove potentially dangerous characters from string inputs"""
            import re
            if not isinstance(value, str):
                return str(value)
            # Only allow alphanumeric, underscore, and hyphen
            return re.sub(f'[^{allowed_chars}]', '', value)
        
        # Sanitize config string values
        if 'city' in config:
            config['city'] = sanitize_string(config['city'])
        if 'driver_selection' in config:
            config['driver_selection'] = sanitize_string(config['driver_selection'])
        if 'driver_id' in config:
            config['driver_id'] = sanitize_string(config['driver_id'])
        if 'force_safety_event' in config:
            config['force_safety_event'] = sanitize_string(config['force_safety_event'])
        if 'certificates_table_name' in config:
            config['certificates_table_name'] = sanitize_string(config['certificates_table_name'])
        
        # Validate account if UI account is provided
        if config.get('ui_account'):
            try:
                import boto3
                if AWS_PROFILE:
                    session = boto3.Session(profile_name=AWS_PROFILE)
                    sts_client = session.client('sts')
                else:
                    sts_client = boto3.client('sts')
                
                identity = sts_client.get_caller_identity()
                simulation_account = identity['Account']
                ui_account = config['ui_account']
                
                if ui_account != simulation_account:
                    return {
                        'error': f'Account mismatch: UI account ({ui_account}) != Simulation account ({simulation_account}). Please restart simulation service with correct AWS profile.',
                        'ui_account': ui_account,
                        'simulation_account': simulation_account
                    }
            except Exception as e:
                print(f"Account validation failed: {e}")
        
        # Prepare simulation command based on vehicle source
        script_dir = os.path.dirname(os.path.abspath(__file__))
        table_suffix = None
        
        if config.get('vehicle_source') in ('real_vehicles', 'real', 'database') or config.get('use_real_vehicles') or isinstance(config.get('vehicles'), list):
            # Use realtime telemetry simulator for real vehicles
            vehicle_count = len(config['vehicles']) if isinstance(config.get('vehicles'), list) else config.get('vehicles', 10)
            
            # Derive table suffix from DEPLOYMENT_STAGE env var
            try:
                deploy_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
                table_suffix = f'{deploy_stage}-storage'
                # Verify the table exists
                import boto3
                session = boto3.Session(profile_name=AWS_PROFILE or 'default')
                ddb_client = session.client('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
                ddb_client.describe_table(TableName=f'cms-{table_suffix}-vehicles')
                print(f"🔍 Detected table suffix: {table_suffix}")
            except Exception as e:
                print(f"⚠️ Could not verify table suffix, using auto-detection: {e}")
                table_suffix = None
                    
            except Exception as e:
                print(f"⚠️ Could not get table suffix from Lambda, using auto-detection: {e}")
                table_suffix = None  # Let simulator auto-detect
            
            cmd = [
                sys.executable, 
                os.path.join(script_dir, 'realtime_telemetry_simulator.py'),
                '--profile', AWS_PROFILE or 'default',
                '--trips', str(config.get('trips', 3)),
                '--vehicles', str(vehicle_count)
            ]
            
            # Add table suffix if we detected one
            if table_suffix:
                cmd.extend(['--table-suffix', table_suffix])
            
            # Certificates table derived from DEPLOYMENT_STAGE in simulator — don't pass UI override
            
            # Add city configuration
            city = config.get('city', 'nyc')
            if city in ['nyc', 'sf', 'chicago', 'miami', 'seattle', 'munich', 'atlanta']:
                cmd.extend(['--city', city])
            elif config.get('city_lat') and config.get('city_lng'):
                cmd.extend(['--city-lat', str(config['city_lat']), '--city-lng', str(config['city_lng'])])
            
            # === ADD FORCED ALERT PARAMETERS ===
            if config.get('force_tire_blowout'):
                cmd.extend(['--force-tire-blowout'])
            if config.get('force_engine_overheat'):
                cmd.extend(['--force-engine-overheat'])
            if config.get('force_battery_critical'):
                cmd.extend(['--force-battery-critical'])
            if config.get('force_brake_failure'):
                cmd.extend(['--force-brake-failure'])
            if config.get('force_oil_pressure_low'):
                cmd.extend(['--force-oil-pressure-low'])
            if config.get('force_hv_battery_degradation'):
                cmd.extend(['--force-hv-battery-degradation'])
            if config.get('force_safety_event') and config['force_safety_event'] not in (None, 'None', 'none', ''):
                cmd.extend(['--force-safety-event', config['force_safety_event']])
            if not config.get('progressive_degradation', True):
                cmd.extend(['--no-progressive-degradation'])
            
            # Pass vehicle configuration if available
            if isinstance(config.get('vehicles'), list):
                import json
                vehicle_config = json.dumps(config['vehicles'])
                cmd.extend(['--vehicle-config', vehicle_config])
            
            # Add driver configuration
            if config.get('driver_selection'):
                cmd.extend(['--driver-selection', config['driver_selection']])
            if config.get('driver_id') and config['driver_id'] not in ('None', 'none', ''):
                cmd.extend(['--driver-id', config['driver_id']])
        else:
            # Use realtime telemetry simulator for generated vehicles too
            vehicle_count = config.get('vehicles', 10)
            
            cmd = [
                sys.executable, 
                os.path.join(script_dir, 'realtime_telemetry_simulator.py'),
                '--profile', AWS_PROFILE or 'default',
                '--trips', str(config.get('trips', 3)),
                '--vehicles', str(vehicle_count),
                '--city', config.get('city', 'seattle')
            ]
        
        if not config.get('cleanup', True):
            cmd.append('--no-cleanup')

        # Add region and IoT rule name — env var takes priority over UI config
        aws_region = os.environ.get('AWS_REGION') or config.get('aws_region') or 'us-east-1'
        cmd.extend(['--region', aws_region])
        # Derive rule name from stage: cms_{stage}_iot_msk_rule
        if table_suffix:
            stage = table_suffix.split('-')[0] if '-' in table_suffix else table_suffix
        else:
            stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        cmd.extend(['--rule-name', f'cms_{stage}_iot_msk_rule'])

        # Add mode parameter (mqtt_direct, can, or fwe)
        mode = config.get('mode', 'mqtt_direct')
        if mode == 'fwe':
            # Add CAN mode to the simulator command
            cmd.extend(['--mode', 'can'])
            # Start FWE containers first
            self._start_fwe_containers(config)
            
            # Build docker run command — simulator must run in Docker for vcan + GPS socket access
            script_dir = os.path.dirname(os.path.abspath(__file__))
            docker_cmd = [
                'docker', 'run', '--rm', '--name', f'cms-sim-{simulation_id[:8]}',
                '--network', 'host', '--privileged',
                '-v', f'{script_dir}:/app',
                '-v', f'{os.path.expanduser("~/.aws")}:/root/.aws:ro',
                '-v', 'fwe-gps-sock:/tmp/fwe-gps',
                '-w', '/app',
                '-e', f'AWS_PROFILE={AWS_PROFILE or "default"}',
                '-e', 'AWS_DEFAULT_REGION=' + os.environ.get('AWS_REGION', 'us-east-1'),
                '-e', 'DEPLOYMENT_STAGE=' + os.environ.get('DEPLOYMENT_STAGE', 'dev'),
                '-e', 'AWS_REGION=' + os.environ.get('AWS_REGION', 'us-east-1'),
            ]
            # Pass vcan/GPS mappings as env vars
            vcan_map = config.get('_vcan_map')
            if vcan_map:
                docker_cmd.extend(['-e', f'FWE_VCAN_MAP={json.dumps(vcan_map)}'])
            gps_sock_map = config.get('_gps_sock_map')
            if gps_sock_map:
                docker_cmd.extend(['-e', f'FWE_GPS_SOCK_MAP={json.dumps(gps_sock_map)}'])
            
            # Check if pre-built sim image exists, otherwise fall back to apt-get/pip
            import subprocess as _sp
            has_sim_image = _sp.run(['docker', 'image', 'inspect', 'cms-sim-local'], capture_output=True).returncode == 0
            
            if has_sim_image:
                docker_cmd.extend([
                    'cms-sim-local', 'python3',
                    *[f'/app/{os.path.basename(a)}' if a.endswith('.py') else a for a in cmd[1:]]
                ])
            else:
                # Build inner command — replace host script path with container path
                inner_args = []
                for a in cmd[1:]:
                    if a.endswith('.py'):
                        inner_args.append(f'/app/{os.path.basename(a)}')
                    else:
                        inner_args.append(a)
                
                docker_cmd.extend([
                    'public.ecr.aws/ubuntu/ubuntu:22.04', 'bash', '-c',
                    'apt-get update -qq && apt-get install -y -qq python3 python3-pip iproute2 >/dev/null 2>&1 && '
                    'pip3 install -q python-can cantools msgpack paho-mqtt boto3 && '
                    'python3 ' + ' '.join(f"'{a}'" for a in inner_args)
                ])
            cmd = docker_cmd
        elif mode in ('can', 'mqtt_direct'):
            cmd.extend(['--mode', mode])

        # Add safety rate parameter
        if 'safety_rate' in config:
            cmd.extend(['--safety-rate', str(config['safety_rate'])])
        
        # Add force maintenance alert if enabled
        if config.get('force_maintenance_alert', False):
            cmd.append('--force-maintenance-alert')
        
        # Start simulation process
        try:
            # Set up environment with AWS profile
            env = os.environ.copy()
            if AWS_PROFILE:
                env['AWS_PROFILE'] = AWS_PROFILE
            
            # Use current workspace directory instead of hardcoded path
            workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            
            # Pass vcan and GPS socket mapping for multi-vehicle FWE
            # (only needed for non-Docker mode; FWE mode passes them via docker -e)

            print(f"🚀 Launching simulator: {' '.join(cmd)}", flush=True)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=workspace_dir,
                env=env
            )
            
            simulation_data = {
                'id': simulation_id,
                'process': process,
                'config': config,
                'status': 'running',
                'start_time': datetime.now(timezone.utc).isoformat(),
                'end_time': None,
                'output': [],
                'error': None,
                'trips': {
                    'total': config.get('trips', 3),
                    'completed': 0,
                    'current_progress': 0.0,
                    'progress': 0.0
                }
            }
            
            self.simulations[simulation_id] = simulation_data
            
            # Start monitoring thread
            monitor_thread = threading.Thread(
                target=self._monitor_simulation,
                args=(simulation_id,)
            )
            monitor_thread.daemon = True
            monitor_thread.start()
            
            return simulation_id
            
        except Exception as e:
            raise Exception(f"Failed to start simulation: {str(e)}")
    
    def _start_fwe_containers(self, config):
        """Start FWE container per vehicle with dedicated vcan interfaces."""
        import subprocess, json as _json
        vehicles = config.get('vehicles', config.get('selected_vehicles', []))
        if isinstance(vehicles, int):
            return  # Generated vehicles, not real — skip FWE

        script_dir = os.path.dirname(os.path.abspath(__file__))
        iot_endpoint = subprocess.check_output(
            ['aws', 'iot', 'describe-endpoint', '--endpoint-type', 'iot:Data-ATS',
             '--query', 'endpointAddress', '--output', 'text'],
            text=True
        ).strip()

        # Generate persistency and populate Docker volume (survives Colima restarts)
        persistency_dir = '/tmp/fwe_e2e_certs/FWE_Persistency'
        if not os.path.exists(f'{persistency_dir}/DecoderManifest.bin'):
            subprocess.run([sys.executable, os.path.join(script_dir, 'generate_fwe_persistency.py'),
                            '--profile', os.environ.get('AWS_PROFILE', 'default'), '--output-dir', persistency_dir],
                           cwd=script_dir)
        # Ensure Docker volume has persistency files (use docker cp for Colima compatibility)
        subprocess.run(['docker', 'volume', 'create', 'fwe-persistency'], capture_output=True)
        helper = subprocess.run(
            ['docker', 'run', '-d', '--name', 'fwe-vol-helper', '-v', 'fwe-persistency:/data',
             'public.ecr.aws/ubuntu/ubuntu:22.04', 'sleep', '30'], capture_output=True)
        if helper.returncode == 0:
            subprocess.run(['docker', 'exec', 'fwe-vol-helper', 'mkdir', '-p', '/data/FWE_Persistency'], capture_output=True)
            for f in ['DecoderManifest.bin', 'CollectionSchemeList.bin']:
                src = os.path.join(persistency_dir, f)
                if os.path.exists(src):
                    subprocess.run(['docker', 'cp', src, f'fwe-vol-helper:/data/FWE_Persistency/{f}'], capture_output=True)
            subprocess.run(['docker', 'stop', 'fwe-vol-helper'], capture_output=True)
            subprocess.run(['docker', 'rm', 'fwe-vol-helper'], capture_output=True)

        for idx, v in enumerate(vehicles):
            vid = v.get('vehicleId', v) if isinstance(v, dict) else v
            vin = v.get('vin', vid) if isinstance(v, dict) else vid
            can_iface = f"vcan{idx}"
            gps_sock_path = f"/tmp/fwe-gps/gps-{idx}.sock"
            container_name = f"cms-fwe-{vin[:12]}"

            # Always populate mappings (simulator needs them even if FWE is already running)
            config.setdefault('_vcan_map', {})[vin] = can_iface
            config.setdefault('_vcan_map', {})[vid] = can_iface
            config.setdefault('_gps_sock_map', {})[vin] = gps_sock_path
            config.setdefault('_gps_sock_map', {})[vid] = gps_sock_path

            # Create vcan interface if needed
            subprocess.run(['docker', 'run', '--rm', '--network', 'host', '--privileged',
                            'alpine', 'sh', '-c',
                            f'ip link show {can_iface} 2>/dev/null || (ip link add dev {can_iface} type vcan && ip link set {can_iface} up)'],
                           capture_output=True)

            # Skip if already running
            result = subprocess.run(['docker', 'ps', '--filter', f'name={container_name}', '-q'],
                                    capture_output=True, text=True)
            if result.stdout.strip():
                print(f"🚗 FWE already running for {vin} on {can_iface} (container: {container_name})")
                continue

            # Get cert from DDB
            try:
                cert_json = subprocess.check_output([
                    sys.executable, '-c', f"""
import boto3, json, sys
ddb = boto3.Session().resource('dynamodb')
t = ddb.Table(f'cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-vehicle-certificates')
resp = t.get_item(Key={{'vehicleId': '{vid}'}})
if 'Item' not in resp:
    resp = t.scan(FilterExpression='vin = :v OR thingName = :v', ExpressionAttributeValues={{':v': '{vin}'}}, Limit=1)
    if not resp.get('Items'): sys.exit(1)
    item = resp['Items'][0]
else:
    item = resp['Item']
print(json.dumps({{'cert': item['certificatePem'], 'key': item['privateKey'], 'thing': item.get('thingName', item.get('vin', '{vin}'))}}))
"""
                ], text=True).strip()
                cert_data = _json.loads(cert_json)
            except Exception as e:
                print(f"⚠️ No cert for {vid}, skipping FWE: {e}")
                continue

            # Start FWE container with dedicated vcan and GPS socket
            # Resolve IoT endpoint IP to work around DNS hanging in Colima QEMU VM
            import socket as _socket
            try:
                iot_ip = _socket.gethostbyname(iot_endpoint)
            except Exception:
                iot_ip = None
            subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True)
            docker_args = [
                'docker', 'run', '--rm', '-d', '--name', container_name,
                '--network', 'host', '--privileged',
            ]
            if iot_ip:
                docker_args.extend(['--add-host', f'{iot_endpoint}:{iot_ip}'])
            docker_args.extend([
                '-e', f'FWE_GPS_SOCKET_PATH={gps_sock_path}',
                '-v', 'fwe-persistency:/var/aws-iot-fleetwise/',
                '-v', 'fwe-gps-sock:/tmp/fwe-gps',
                'public.ecr.aws/s0o2j8p0/cms-fwe-gps:latest',
                '--vehicle-name', cert_data['thing'],
                '--endpoint-url', iot_endpoint,
                '--iotfleetwise-topic-prefix', 'cms/fleetwise/',
                '--can-bus0', can_iface,
                '--certificate', cert_data['cert'],
                '--private-key', cert_data['key'],
                '--log-level', 'Info',
                '--persistency-path', '/var/aws-iot-fleetwise/'
            ])
            subprocess.run(docker_args)
            print(f"🚗 FWE started for {vin} on {can_iface} (container: {container_name})")

        # Wait for FWE containers to activate collection schemes before sim sends data
        import time as _time
        started_containers = [f"cms-fwe-{(v.get('vin', v) if isinstance(v, dict) else v)[:12]}" for v in vehicles]
        for attempt in range(30):
            all_ready = True
            for cname in started_containers:
                result = subprocess.run(['docker', 'logs', cname], capture_output=True, text=True)
                if 'activated collection schemes' not in result.stdout:
                    all_ready = False
                    break
            if all_ready:
                print(f"✅ All FWE containers ready (took ~{attempt * 2}s)")
                break
            _time.sleep(2)
        else:
            print("⚠️ FWE readiness timeout — proceeding anyway")

    def _monitor_simulation(self, simulation_id: str):
        """Monitor simulation process"""
        simulation = self.simulations.get(simulation_id)
        if not simulation:
            return
        
        process = simulation['process']
        
        try:
            # Read output in real-time
            while process.poll() is None:
                output = process.stdout.readline()
                if output:
                    message = output.strip()
                    simulation['output'].append({
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'message': message
                    })
                    
                    # Check for trip completion or progress
                    if ("Trip" in message and "completed" in message) or ("Trip progress:" in message):
                        self._update_trip_progress(simulation_id, message)
                        
                time.sleep(0.1)
            
            # Get final output
            stdout, stderr = process.communicate()
            if stdout:
                for line in stdout.split('\n'):
                    if line.strip():
                        simulation['output'].append({
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                            'message': line.strip()
                        })
            
            # Update status
            simulation['status'] = 'completed' if process.returncode == 0 else 'failed'
            simulation['end_time'] = datetime.now(timezone.utc).isoformat()
            
            if stderr and process.returncode != 0:
                simulation['error'] = stderr
                
        except Exception as e:
            simulation['status'] = 'failed'
            simulation['error'] = str(e)
            simulation['end_time'] = datetime.now(timezone.utc).isoformat()
    
    def _update_trip_progress(self, simulation_id: str, message: str):
        """Update trip progress based on log message"""
        simulation = self.simulations.get(simulation_id)
        if not simulation:
            return
            
        import re
        
        # Parse trip completion from message like "✅ Trip 1/3 completed for VEH-1759242552"
        completion_match = re.search(r'Trip (\d+)/(\d+) completed', message)
        if completion_match:
            completed = int(completion_match.group(1))
            total = int(completion_match.group(2))
            
            simulation['trips']['completed'] = completed
            simulation['trips']['total'] = total
            simulation['trips']['current_progress'] = 0.0  # Reset current trip progress
            
        # Parse current trip progress like "Trip progress: 23.8%"
        progress_match = re.search(r'Trip progress: ([\d.]+)%', message)
        if progress_match:
            current_progress = float(progress_match.group(1))
            simulation['trips']['current_progress'] = current_progress
            
        # Calculate overall progress: completed trips + current trip progress
        completed = simulation['trips']['completed']
        total = simulation['trips']['total']
        current_progress = simulation['trips'].get('current_progress', 0.0)
        
        overall_progress = ((completed + (current_progress / 100.0)) / total) * 100.0
        simulation['trips']['progress'] = min(100.0, overall_progress)
    
    def stop_simulation(self, simulation_id: str) -> bool:
        """Stop a running simulation"""
        simulation = self.simulations.get(simulation_id)
        if not simulation:
            return False
        
        if simulation['status'] == 'running':
            try:
                simulation['process'].terminate()
                simulation['status'] = 'stopped'
                simulation['end_time'] = datetime.now(timezone.utc).isoformat()
                return True
            except Exception:
                return False
        
        return False
    
    def get_simulation_status(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Get simulation status"""
        simulation = self.simulations.get(simulation_id)
        if not simulation:
            return None
        
        # Don't include the process object in the response
        response = {k: v for k, v in simulation.items() if k != 'process'}
        
        # Calculate trip progress
        config = simulation.get('config', {})
        trips_per_vehicle = config.get('trips', 3)
        
        if config.get('vehicle_source') == 'real_vehicles':
            vehicle_count = len(config.get('selected_vehicles', []))
        elif isinstance(config.get('vehicles'), list):
            vehicle_count = len(config.get('vehicles', []))
        else:
            vehicle_count = config.get('vehicles', 10)
        
        total_trips = trips_per_vehicle * vehicle_count
        
        # Estimate completed trips based on elapsed time
        if simulation.get('start_time'):
            start_time = datetime.fromisoformat(simulation['start_time'].replace('Z', '+00:00'))
            elapsed_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            # Each trip estimated to take 30 minutes (1800 seconds)
            estimated_trip_duration = 1800
            estimated_completed_trips = min(
                int(elapsed_seconds / estimated_trip_duration * vehicle_count), 
                total_trips
            )
        else:
            estimated_completed_trips = 0
        
        # Add trip progress to response
        response['trips_completed'] = estimated_completed_trips
        response['total_trips'] = total_trips
        response['progress_percentage'] = round((estimated_completed_trips / total_trips) * 100, 1) if total_trips > 0 else 0
        
        return response
    
    def list_simulations(self) -> List[Dict[str, Any]]:
        """List all simulations"""
        result = []
        for sim in self.simulations.values():
            # Get basic simulation data without process
            sim_data = {k: v for k, v in sim.items() if k != 'process'}
            
            # Add trip progress calculation
            config = sim.get('config', {})
            trips_per_vehicle = config.get('trips', 3)
            
            if isinstance(config.get('vehicles'), list):
                vehicle_count = len(config.get('vehicles', []))
            else:
                vehicle_count = config.get('vehicles', 10)
            
            total_trips = trips_per_vehicle * vehicle_count
            
            # Estimate completed trips based on elapsed time
            if sim.get('start_time'):
                start_time = datetime.fromisoformat(sim['start_time'].replace('Z', '+00:00'))
                elapsed_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
                
                # Each trip estimated to take 30 minutes (1800 seconds)
                estimated_trip_duration = 1800
                estimated_completed_trips = min(
                    int(elapsed_seconds / estimated_trip_duration * vehicle_count), 
                    total_trips
                )
            else:
                estimated_completed_trips = 0
            
            # Add trip progress to simulation data
            sim_data['trips_completed'] = estimated_completed_trips
            sim_data['total_trips'] = total_trips
            sim_data['progress_percentage'] = round((estimated_completed_trips / total_trips) * 100, 1) if total_trips > 0 else 0
            
            result.append(sim_data)
        
        return result

# Global simulation manager
sim_manager = SimulationManager()

@app.route('/api/simulation/logs/<simulation_id>')
def stream_logs(simulation_id):
    """Stream simulation logs in real-time"""
    def generate():
        simulation = sim_manager.simulations.get(simulation_id)
        if not simulation:
            yield f"data: {json.dumps({'error': 'Simulation not found'})}\n\n"
            return
        
        # Send existing output first
        for line in simulation.get('output', []):
            yield f"data: {json.dumps({'log': line, 'timestamp': datetime.now().isoformat()})}\n\n"
        
        # Stream new output
        last_output_count = len(simulation.get('output', []))
        while simulation.get('status') == 'running':
            time.sleep(1)
            simulation = sim_manager.simulations.get(simulation_id)
            if not simulation:
                break
                
            current_output = simulation.get('output', [])
            if len(current_output) > last_output_count:
                # Send new lines
                for line in current_output[last_output_count:]:
                    yield f"data: {json.dumps({'log': line, 'timestamp': datetime.now().isoformat()})}\n\n"
                last_output_count = len(current_output)
        
        # Send final status
        yield f"data: {json.dumps({'status': 'completed', 'timestamp': datetime.now().isoformat()})}\n\n"
    
    return app.response_class(generate(), mimetype='text/plain')

@app.route('/api/simulation/logs/<simulation_id>/stream')
def stream_logs_sse(simulation_id):
    """Stream simulation logs using Server-Sent Events"""
    def generate():
        simulation = sim_manager.simulations.get(simulation_id)
        if not simulation:
            yield f"data: {json.dumps({'error': 'Simulation not found'})}\n\n"
            return
        
        # Send existing output first
        for line in simulation.get('output', []):
            yield f"data: {json.dumps({'log': line, 'timestamp': datetime.now().isoformat()})}\n\n"
        
        # Stream new output
        last_output_count = len(simulation.get('output', []))
        while True:
            simulation = sim_manager.simulations.get(simulation_id)
            if not simulation or simulation.get('status') != 'running':
                yield f"data: {json.dumps({'status': 'completed', 'timestamp': datetime.now().isoformat()})}\n\n"
                break
                
            current_output = simulation.get('output', [])
            if len(current_output) > last_output_count:
                # Send new lines
                for line in current_output[last_output_count:]:
                    yield f"data: {json.dumps({'log': line, 'timestamp': datetime.now().isoformat()})}\n\n"
                last_output_count = len(current_output)
            
            time.sleep(1)
    
    return app.response_class(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
    )
def discover_iot_endpoint():
    """Discover IoT endpoint for the selected AWS profile"""
    try:
        import boto3
        
        # Get region from query parameter or default to us-east-1
        region = request.args.get('region', os.environ.get('AWS_REGION', 'us-east-1'))
        
        # Create session with selected profile
        if AWS_PROFILE:
            session = boto3.Session(profile_name=AWS_PROFILE)
            iot_client = session.client('iot', region_name=region)
            sts_client = session.client('sts', region_name=region)
        else:
            iot_client = boto3.client('iot', region_name=region)
            sts_client = boto3.client('sts', region_name=region)
        
        # Get account ID
        identity = sts_client.get_caller_identity()
        account_id = identity['Account']
        
        # Get IoT endpoint
        response = iot_client.describe_endpoint(endpointType='iot:Data-ATS')
        endpoint = response['endpointAddress']
        
        return jsonify({
            'success': True,
            'endpoint': endpoint,
            'region': region,
            'profile': AWS_PROFILE or 'default',
            'account': account_id
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/simulation/start', methods=['POST'])
def start_simulation():
    """Start a new fleet simulation"""
    try:
        config = request.get_json()
        
        # Validate configuration
        if not config:
            return jsonify({'error': 'Configuration required'}), 400
        
        # Validate required fields
        if not config.get('vehicle_source'):
            return jsonify({'error': 'Vehicle source must be selected'}), 400
        
        # Set defaults and validate
        config.setdefault('trips', 3)
        config.setdefault('vehicles', 10)
        config.setdefault('city', 'seattle')
        config.setdefault('safety_rate', 0.15)
        config.setdefault('interval', 30)
        config.setdefault('fleet_prefix', 'SIM')
        config.setdefault('cleanup', True)
        config.setdefault('iot_endpoint', None)
        config.setdefault('aws_region', 'us-east-1')
        config.setdefault('driver_id', None)  # Optional specific driver
        config.setdefault('driver_selection', 'random')  # 'random', 'consistent', or 'specific'
        
        # === FORCED ALERT PARAMETERS ===
        config.setdefault('force_tire_blowout', False)  # Force tire pressure critical
        config.setdefault('force_engine_overheat', False)  # Force engine overheating
        config.setdefault('force_battery_critical', False)  # Force battery failure
        config.setdefault('force_brake_failure', False)  # Force brake system issues
        config.setdefault('force_oil_pressure_low', False)  # Force oil pressure low
        config.setdefault('force_hv_battery_degradation', False)  # Force EV battery degradation
        config.setdefault('force_safety_event', None)  # 'hard_braking', 'collision_avoidance', 'seatbelt_violation', 'phone_usage'
        config.setdefault('progressive_degradation', True)
        config.setdefault('mode', 'mqtt_direct')  # 'mqtt_direct' or 'can'
        
        # Convert values to proper types (handle form data)
        def safe_convert(value, convert_func, default):
            try:
                if isinstance(value, list) and len(value) > 0:
                    return convert_func(value[0])
                elif isinstance(value, (str, int, float)):
                    return convert_func(value)
                else:
                    return default
            except:
                return default
        
        config['trips'] = safe_convert(config['trips'], int, 3)
        config['safety_rate'] = safe_convert(config['safety_rate'], float, 0.15)
        config['interval'] = safe_convert(config['interval'], int, 30)
        
        # Handle vehicles - preserve array for real vehicles, convert to int for generated
        if isinstance(config.get('vehicles'), list):
            # Real vehicles - keep the array
            vehicle_count = len(config['vehicles'])
        else:
            # Generated vehicles - convert to int
            config['vehicles'] = safe_convert(config['vehicles'], int, 10)
            vehicle_count = config['vehicles']
        
        # Validate ranges
        if not 1 <= config['trips'] <= 20:  # Max 20 trips per vehicle
            return jsonify({'error': 'Duration must be between 1 and 480 minutes'}), 400
        
        if not 1 <= vehicle_count <= 100:
            return jsonify({'error': 'Vehicle count must be between 1 and 100'}), 400
        
        if not 0.0 <= config['safety_rate'] <= 1.0:
            return jsonify({'error': 'Safety rate must be between 0.0 and 1.0'}), 400
        
        # Start simulation
        result = sim_manager.start_simulation(config)
        
        # Check if result is an error (account mismatch)
        if isinstance(result, dict) and 'error' in result:
            return jsonify(result), 400
        
        return jsonify({
            'success': True,
            'simulation_id': result,
            'message': 'Simulation started successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulation/stop/<simulation_id>', methods=['POST'])
def stop_simulation(simulation_id: str):
    """Stop a running simulation"""
    try:
        success = sim_manager.stop_simulation(simulation_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Simulation stopped successfully'
            })
        else:
            return jsonify({'error': 'Simulation not found or not running'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulation/status/<simulation_id>', methods=['GET'])
def get_simulation_status(simulation_id: str):
    """Get simulation status"""
    try:
        status = sim_manager.get_simulation_status(simulation_id)
        
        if status:
            return jsonify(status)
        else:
            return jsonify({'error': 'Simulation not found'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulation/list', methods=['GET'])
def list_simulations():
    """List all simulations"""
    try:
        simulations = sim_manager.list_simulations()
        return jsonify({'simulations': simulations})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/simulation/presets', methods=['GET'])
def get_simulation_presets():
    """Get predefined simulation configurations"""
    presets = [
        {
            'id': 'quick_test',
            'name': 'Quick Test',
            'description': '1 trip per vehicle with 3 vehicles and high safety event rate',
            'config': {
                'trips': 1,
                'vehicles': 3,
                'city': 'seattle',
                'safety_rate': 0.4,
                'interval': 15,
                'fleet_prefix': 'TEST',
                'cleanup': True
            }
        },
        {
            'id': 'standard_demo',
            'name': 'Standard Demo',
            'description': '3 trips per vehicle with 10 vehicles and normal safety events',
            'config': {
                'trips': 3,
                'vehicles': 10,
                'city': 'seattle',
                'safety_rate': 0.15,
                'interval': 30,
                'fleet_prefix': 'DEMO',
                'cleanup': True
            }
        },
        {
            'id': 'safety_focus',
            'name': 'Safety Event Focus',
            'description': '2 trips per vehicle with high safety event rate for testing',
            'config': {
                'trips': 2,
                'vehicles': 8,
                'city': 'seattle',
                'safety_rate': 0.35,
                'interval': 20,
                'fleet_prefix': 'SAFE',
                'cleanup': True
            }
        },
        {
            'id': 'load_test',
            'name': 'Load Test',
            'description': '5 trips per vehicle with many vehicles for performance testing',
            'config': {
                'trips': 5,
                'vehicles': 25,
                'city': 'seattle',
                'safety_rate': 0.1,
                'interval': 30,
                'fleet_prefix': 'LOAD',
                'cleanup': True
            }
        }
    ]
    
    return jsonify({'presets': presets})

@app.route('/api/safety-events', methods=['GET'])
def get_safety_events():
    """Get recent safety events from all running simulations"""
    try:
        all_events = []
        current_time = datetime.now(timezone.utc)
        
        # Get events from all running simulations
        for sim_id, simulation in sim_manager.simulations.items():
            if simulation['status'] == 'running':
                # Extract safety events from simulation logs
                for log_entry in simulation.get('logs', [])[-100:]:  # Last 100 log entries
                    message = log_entry.get('message', '')
                    timestamp = log_entry.get('timestamp', '')
                    
                    # Parse safety event messages
                    if '⚠️  Safety event:' in message:
                        try:
                            # Extract event type and vehicle ID from message
                            # Format: "⚠️  Safety event: hard_braking - Vehicle 1FLEET0000SIM012"
                            parts = message.split('Safety event: ')[1].split(' - Vehicle ')
                            event_type = parts[0].strip()
                            vehicle_id = parts[1].strip()
                            
                            # Map simulation event types to dashboard event types
                            event_type_mapping = {
                                'hard_braking': 'hard_braking',
                                'lane_departure_violation': 'lane_departure',
                                'rapid_acceleration': 'rapid_acceleration',
                                'speeding_violation': 'speeding',
                                'harsh_cornering': 'hard_braking',  # Map to hard_braking for now
                                'collision_warning': 'hard_braking'  # Map to hard_braking for now
                            }
                            
                            mapped_event_type = event_type_mapping.get(event_type, 'hard_braking')
                            
                            # Create safety event object
                            safety_event = {
                                'id': f"{vehicle_id}-{event_type}-{timestamp}",
                                'vehicleId': vehicle_id,
                                'vin': vehicle_id,
                                'eventType': mapped_event_type,
                                'severity': 'medium',  # Default severity
                                'timestamp': timestamp,
                                'location': {
                                    'lat': 40.7128 + (hash(vehicle_id) % 100) / 1000,  # Mock location
                                    'lon': -74.0060 + (hash(vehicle_id) % 100) / 1000
                                },
                                'details': {
                                    'speed': 45 + (hash(vehicle_id) % 20),
                                    'description': f"{event_type.replace('_', ' ').title()} detected"
                                }
                            }
                            
                            all_events.append(safety_event)
                            
                        except (IndexError, AttributeError) as e:
                            # Skip malformed log entries
                            continue
        
        # Sort events by timestamp (most recent first)
        all_events.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # Return the most recent 50 events
        return jsonify(all_events[:50])
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'active_simulations': len([s for s in sim_manager.simulations.values() if s['status'] == 'running'])
    })


if __name__ == '__main__':
    print("🚀 Fleet Simulation API Server")
    print("=" * 35)
    
    # Select AWS profile on startup
    if not select_aws_profile():
        print("❌ Failed to configure AWS profile. Exiting.")
        sys.exit(1)
    
    print(f"\n🌐 Starting API server on http://localhost:5001")
    print(f"📡 Using AWS profile: {AWS_PROFILE}")
    print("Endpoints:")
    print("  POST /api/simulation/start")
    print("  POST /api/simulation/stop/<id>")
    print("  GET  /api/simulation/status/<id>")
    print("  GET  /api/simulation/list")
    print("  GET  /api/simulation/presets")
    print("  GET  /api/simulation/drivers")
    print("  GET  /api/simulation/discover-iot-endpoint")
    print("  GET  /health")
    print("🔄 Press Ctrl+C to stop")
    
    app.run(host='0.0.0.0', port=5001, debug=False)
