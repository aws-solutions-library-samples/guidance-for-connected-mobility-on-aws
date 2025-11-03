"""Lambda handler for Ford FCS consumer with Atlanta GPS enhancement"""
import json
import os
import time
import socket
from datetime import datetime
from atlanta_route_generator import AtlantaRouteGenerator
from kafka import KafkaProducer
from kafka.sasl.oauth import AbstractTokenProvider
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

# Simulated Ford telemetry
vehicle_routes = {}
producer = None

class MSKTokenProvider(AbstractTokenProvider):
    def token(self):
        token, _ = MSKAuthTokenProvider.generate_auth_token('us-east-1')
        return token

def get_producer():
    """Get or create Kafka producer - following AWS docs exactly"""
    global producer
    if producer is None:
        tp = MSKTokenProvider()
        producer = KafkaProducer(
            bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'].split(','),
            security_protocol='SASL_SSL',
            sasl_mechanism='OAUTHBEARER',
            sasl_oauth_token_provider=tp,
            client_id=socket.gethostname(),
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
    return producer

def lambda_handler(event, context):
    """
    Lambda handler - processes Ford telemetry and adds GPS
    For testing: generates sample data with Atlanta routes
    """
    max_runtime = 600
    start_time = time.time()
    
    messages_processed = 0
    messages_enhanced = 0
    messages_written = 0
    
    try:
        prod = get_producer()
        topic = os.environ.get('MSK_TOPIC', 'cms-telemetry-oem')
        
        # Get vehicle ID and message count from event
        vehicle_id = event.get('vehicleId', 'ec64c899-897c-4e88-801e-12b7995ed05d')
        message_count = int(event.get('messageCount', 10))
        
        # For testing: generate sample Ford telemetry
        sample_messages = generate_sample_ford_telemetry(vehicle_id, message_count)
        
        # Log first two messages for debugging
        if len(sample_messages) > 0:
            print(f"Message 1 (IGNITION): {json.dumps(sample_messages[0], indent=2)}")
        if len(sample_messages) > 1:
            print(f"Message 2 (SPEED+GPS): {json.dumps(sample_messages[1], indent=2)}")
        
        for message in sample_messages:
            # Enhance with GPS
            enhanced = enhance_with_gps(message)
            if enhanced:
                messages_enhanced += 1
            
            # Write to MSK (following AWS example pattern)
            try:
                future = prod.send(topic, value=message)
                prod.flush()
                record_metadata = future.get(timeout=10)
                messages_written += 1
                print(f"Sent to MSK topic {record_metadata.topic} partition {record_metadata.partition}: {message['vehicleId']}")
            except Exception as e:
                print(f"Failed to send: {e}")
            
            messages_processed += 1
            
            if time.time() - start_time > max_runtime:
                break
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'messages_processed': messages_processed,
                'messages_enhanced': messages_enhanced,
                'messages_written': messages_written,
                'runtime_seconds': time.time() - start_time
            })
        }
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

def generate_sample_ford_telemetry(vehicle_id='VEH-FORD-001', message_count=10):
    """Generate sample Ford FCS format telemetry with IGNITION_STATUS and GPS"""
    messages = []
    
    # Initialize route generator for this vehicle
    if vehicle_id not in vehicle_routes:
        route_name = "downtown_to_airport" if hash(vehicle_id) % 2 == 0 else "buckhead_to_midtown"
        vehicle_routes[vehicle_id] = AtlantaRouteGenerator(route_name)
    
    route_gen = vehicle_routes[vehicle_id]
    
    for i in range(message_count):
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # Alternate between SPEED and IGNITION_STATUS
        if i == 0:
            # Start with ignition ON
            messages.append({
                "vehicleId": vehicle_id,
                "timestamp": timestamp,
                "oem_source": "ford",
                "typedData": {
                    "@type": "type.googleapis.com/autonomic.ext.telemetry.Metric",
                    "signal": {"wksSignal": "IGNITION_STATUS"},
                    "startTime": timestamp,
                    "metricKind": "GAUGE",
                    "enumValue": {"ignitionStatus": "ON"}
                }
            })
        elif i == message_count - 1:
            # End with ignition OFF
            messages.append({
                "vehicleId": vehicle_id,
                "timestamp": timestamp,
                "oem_source": "ford",
                "typedData": {
                    "@type": "type.googleapis.com/autonomic.ext.telemetry.Metric",
                    "signal": {"wksSignal": "IGNITION_STATUS"},
                    "startTime": timestamp,
                    "metricKind": "GAUGE",
                    "enumValue": {"ignitionStatus": "OFF"}
                }
            })
        else:
            # SPEED signals with GPS
            speed_mph = 15.6 + (i * 2.2)
            position = route_gen.get_next_position(speed_mph=speed_mph, timestamp=timestamp)
            
            messages.append({
                "vehicleId": vehicle_id,
                "timestamp": timestamp,
                "oem_source": "ford",
                "typedData": {
                    "@type": "type.googleapis.com/autonomic.ext.telemetry.Metric",
                    "signal": {"wksSignal": "SPEED"},
                    "startTime": timestamp,
                    "metricKind": "GAUGE",
                    "speedValue": {
                        "speed": speed_mph,
                        "uncertainty": 0.5,
                        "detectionType": "SPEED_WHEEL_TICKS"
                    },
                    "location": {
                        "latitude": position['latitude'],
                        "longitude": position['longitude'],
                        "heading": position['heading']
                    }
                }
            })
    
    return messages

def enhance_with_gps(message):
    """Add GPS coordinates based on Atlanta route"""
    vehicle_id = message.get('vehicleId')
    
    if 'latitude' in message and 'longitude' in message:
        return False
    
    # Get or create route for vehicle
    if vehicle_id not in vehicle_routes:
        route_name = "downtown_to_airport" if hash(vehicle_id) % 2 == 0 else "buckhead_to_midtown"
        vehicle_routes[vehicle_id] = AtlantaRouteGenerator(route_name)
    
    route_gen = vehicle_routes[vehicle_id]
    
    # Convert speed from m/s to mph
    speed_mps = message.get('spd', 0)
    speed_mph = speed_mps * 2.237 if speed_mps else 35
    
    # Get GPS position
    position = route_gen.get_next_position(
        speed_mph=speed_mph,
        timestamp=message.get('timestamp')
    )
    
    # Add to message
    message['latitude'] = position['latitude']
    message['longitude'] = position['longitude']
    message['heading'] = position['heading']
    message['gps_source'] = 'synthetic_atlanta'
    message['location_name'] = position['location_name']
    
    return True

# For local testing
if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(json.dumps(result, indent=2))
