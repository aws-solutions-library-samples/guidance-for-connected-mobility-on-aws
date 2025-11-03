"""
Kafka writer for OEM telemetry data
Writes raw OEM messages to MSK with IAM authentication
"""
import json
from kafka import KafkaProducer
from kafka.sasl.oauth import AbstractTokenProvider
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider
from typing import Dict, Any

class MSKTokenProvider(AbstractTokenProvider):
    """MSK IAM token provider"""
    def __init__(self, **config):
        self.region = config.get('region', 'us-east-1')
    
    def token(self):
        token, _ = MSKAuthTokenProvider.generate_auth_token(self.region)
        return token

class KafkaWriter:
    def __init__(self, bootstrap_servers: str, topic: str, oem_name: str):
        self.topic = topic
        self.oem_name = oem_name
        
        # MSK IAM authentication configuration
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers.split(','),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            compression_type='gzip',
            acks='all',
            retries=3,
            security_protocol='SASL_SSL',
            sasl_mechanism='OAUTHBEARER',
            sasl_oauth_token_provider=MSKTokenProvider(region='us-east-1')
        )
        
        print(f"✓ Kafka producer connected to {bootstrap_servers}")
    
    def write_message(self, message: Dict[str, Any], vin: str = None):
        """Write a single message to MSK"""
        try:
            # Extract VIN for partitioning
            if not vin:
                vin = self._extract_vin(message)
            
            # Add metadata
            message_with_metadata = {
                'oem': self.oem_name,
                'raw_data': message
            }
            
            # Send to Kafka
            future = self.producer.send(
                self.topic,
                key=vin,
                value=message_with_metadata,
                headers=[
                    ('oem', self.oem_name.encode('utf-8')),
                    ('source_type', 'grpc'.encode('utf-8'))
                ]
            )
            
            # Wait for confirmation
            record_metadata = future.get(timeout=10)
            print(f"✓ Message written to partition {record_metadata.partition}")
            
            return True
        except Exception as e:
            print(f"✗ Failed to write message: {e}")
            return False
    
    def _extract_vin(self, message: Dict[str, Any]) -> str:
        """Extract VIN from message for partitioning"""
        # Try common VIN locations
        if 'vin' in message:
            return message['vin']
        elif 'assetInfo' in message:
            return message['assetInfo'].get('vehicleAssetInfo', {}).get('vin', 'unknown')
        elif 'vehicle_id' in message:
            return message['vehicle_id']
        else:
            return 'unknown'
    
    def flush(self):
        """Flush pending messages"""
        self.producer.flush()
    
    def close(self):
        """Close producer connection"""
        self.producer.close()
        print("✓ Kafka producer closed")
