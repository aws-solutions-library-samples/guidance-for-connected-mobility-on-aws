"""
OEM Consumer Entry Point
Loads configuration and starts appropriate consumer
"""
import sys
import os
import json
import base64
from datetime import datetime
from config import OEMConfig
from kafka_writer import KafkaWriter
from consumers.grpc_consumer import GRPCConsumer

def convert_bytes_to_base64(obj):
    """Recursively convert bytes to base64 strings for JSON serialization"""
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode('utf-8')
    elif isinstance(obj, dict):
        return {k: convert_bytes_to_base64(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_bytes_to_base64(item) for item in obj]
    return obj

class MockKafkaWriter:
    """Mock Kafka writer for testing without MSK access"""
    def __init__(self, topic, oem_name):
        self.topic = topic
        self.oem_name = oem_name
        self.message_count = 0
        
        # Create logs directory and output file
        os.makedirs("logs", exist_ok=True)
        self.log_file = f"logs/ford_messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        print(f"✓ Mock Kafka writer (messages will be printed and saved to {self.log_file})")
    
    def write_message(self, message, vin=None):
        self.message_count += 1
        
        # Print to console
        print(f"\n📨 Message #{self.message_count}")
        print(f"   VIN: {vin}")
        print(f"   OEM: {self.oem_name}")
        print(f"   Type: {message.get('typedData', {}).get('@type', 'unknown')}")
        
        # Print first few fields of typedData for readability
        typed_data = message.get('typedData', {})
        if typed_data:
            print(f"   Data preview:")
            for key, value in list(typed_data.items())[:5]:
                if key != '@type':
                    print(f"      {key}: {str(value)[:100]}")
        
        # Save full message to JSON file
        try:
            with open(self.log_file, 'a') as f:
                json.dump({
                    'message_number': self.message_count,
                    'vin': vin,
                    'oem': self.oem_name,
                    'message': convert_bytes_to_base64(message)
                }, f)
                f.write('\n')
        except Exception as e:
            print(f"⚠ Failed to write to log file: {e}")
        
        return True
    
    def flush(self):
        print(f"✓ Flushed {self.message_count} messages")
    
    def close(self):
        print(f"✓ Mock writer closed ({self.message_count} total messages)")

def main():
    print("=" * 60)
    print("🚀 OEM Consumer Starting")
    print("=" * 60)
    
    # Load configuration
    config = OEMConfig()
    print(f"✓ OEM: {config.oem_name}")
    print(f"✓ Connection Type: {config.connection_type}")
    print(f"✓ MSK Topic: {config.msk_topic}")
    
    # Check if test mode (no MSK access)
    test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
    
    # Initialize Kafka writer
    if test_mode or not config.msk_bootstrap_servers:
        print("⚠ TEST MODE: Messages will be printed, not sent to MSK")
        kafka_writer = MockKafkaWriter(
            topic=config.msk_topic,
            oem_name=config.oem_name
        )
    else:
        kafka_writer = KafkaWriter(
            bootstrap_servers=config.msk_bootstrap_servers,
            topic=config.msk_topic,
            oem_name=config.oem_name
        )
    
    # Get connection configuration
    connection_config = config.get_connection_config()
    assigned_shards = config.get_shard_assignment()
    
    # Start appropriate consumer
    try:
        if config.connection_type == 'grpc':
            print("📡 Starting gRPC consumer...")
            consumer = GRPCConsumer(connection_config, kafka_writer, assigned_shards)
            consumer.consume()
        
        elif config.connection_type == 'rest':
            print("🔄 Starting REST consumer...")
            # TODO: Implement REST consumer
            print("⚠ REST consumer not yet implemented")
        
        elif config.connection_type == 'websocket':
            print("🌐 Starting WebSocket consumer...")
            # TODO: Implement WebSocket consumer
            print("⚠ WebSocket consumer not yet implemented")
        
        else:
            print(f"✗ Unknown connection type: {config.connection_type}")
            sys.exit(1)
    
    except KeyboardInterrupt:
        print("\n⏹ Shutting down gracefully...")
        kafka_writer.flush()
        kafka_writer.close()
        print("✓ Consumer stopped")
    
    except Exception as e:
        print(f"✗ Fatal error: {e}")
        kafka_writer.close()
        sys.exit(1)

if __name__ == '__main__':
    main()
