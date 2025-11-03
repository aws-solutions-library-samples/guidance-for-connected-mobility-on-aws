"""Lambda handler with GPS enhancement for Ford telemetry"""
import json
import os
import time
from datetime import datetime
from consumers.ford_consumer import FordConsumer
from kafka_writer import KafkaWriter
from vehicle_registration import VehicleRegistration
from atlanta_route_generator import AtlantaRouteGenerator

# Route generators per vehicle (persistent across invocations)
vehicle_routes = {}

def lambda_handler(event, context):
    """Lambda handler with GPS injection"""
    max_runtime = 600  # 10 minutes
    start_time = time.time()
    
    kafka_writer = KafkaWriter(
        bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'],
        topic=os.environ.get('MSK_TOPIC', 'cms-telemetry-oem')
    )
    
    vehicle_reg = VehicleRegistration(
        table_name=os.environ.get('VEHICLES_TABLE', 'cms-dev-storage-vehicles')
    )
    
    consumer = FordConsumer(
        kafka_writer=kafka_writer,
        vehicle_registration=vehicle_reg
    )
    
    messages_processed = 0
    messages_enhanced = 0
    
    try:
        for message in consumer.consume_with_timeout(max_runtime):
            # Enhance message with GPS if missing
            enhanced = enhance_with_gps(message)
            if enhanced:
                messages_enhanced += 1
            
            messages_processed += 1
            
            if time.time() - start_time > max_runtime:
                break
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'messages_processed': messages_processed,
                'messages_enhanced': messages_enhanced,
                'runtime_seconds': time.time() - start_time
            })
        }
    
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
    
    finally:
        consumer.close()
        kafka_writer.close()

def enhance_with_gps(message):
    """
    Add GPS coordinates to Ford message if missing
    Uses Atlanta route based on vehicle speed and heading
    """
    vehicle_id = message.get('vehicleId')
    
    # Check if message already has GPS
    if 'latitude' in message and 'longitude' in message:
        return False
    
    # Get or create route generator for this vehicle
    if vehicle_id not in vehicle_routes:
        # Assign different routes to different vehicles
        route_name = "downtown_to_airport" if hash(vehicle_id) % 2 == 0 else "buckhead_to_midtown"
        vehicle_routes[vehicle_id] = AtlantaRouteGenerator(route_name)
    
    route_gen = vehicle_routes[vehicle_id]
    
    # Get speed from Ford data (convert from m/s to mph if needed)
    speed_mps = message.get('spd', 0)  # CMS format after transform
    speed_mph = speed_mps * 2.237 if speed_mps else 35  # Default 35 mph
    
    # Generate GPS position
    position = route_gen.get_next_position(
        speed_mph=speed_mph,
        timestamp=message.get('timestamp')
    )
    
    # Add GPS to message
    message['latitude'] = position['latitude']
    message['longitude'] = position['longitude']
    message['heading'] = position.get('heading', message.get('heading', 0))
    message['gps_source'] = 'synthetic_atlanta'
    message['location_name'] = position['location_name']
    
    print(f"Enhanced {vehicle_id} with GPS: {position['location_name']}")
    
    return True
