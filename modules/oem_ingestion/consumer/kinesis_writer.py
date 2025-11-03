"""Kinesis writer as MSK proxy - simpler for Lambda"""
import json
import boto3
import base64
import gzip

class KinesisWriter:
    """Write to Kinesis stream which can be connected to MSK"""
    
    def __init__(self, stream_name='ford-telemetry-stream'):
        self.stream_name = stream_name
        self.client = boto3.client('kinesis', region_name='us-east-1')
        
    def write(self, message):
        """Write message to Kinesis"""
        try:
            # Compress and encode like CMS format
            json_str = json.dumps(message)
            compressed = gzip.compress(json_str.encode('utf-8'))
            encoded = base64.b64encode(compressed).decode('utf-8')
            
            response = self.client.put_record(
                StreamName=self.stream_name,
                Data=encoded,
                PartitionKey=message.get('vehicleId', 'default')
            )
            return True
        except Exception as e:
            print(f"Failed to write to Kinesis: {e}")
            # Fallback: write uncompressed
            try:
                response = self.client.put_record(
                    StreamName=self.stream_name,
                    Data=json.dumps(message),
                    PartitionKey=message.get('vehicleId', 'default')
                )
                return True
            except Exception as e2:
                print(f"Fallback also failed: {e2}")
                return False
    
    def flush(self):
        """No-op for Kinesis"""
        pass
    
    def close(self):
        """No-op for Kinesis"""
        pass
