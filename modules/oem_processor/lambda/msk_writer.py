"""Simple MSK writer using kafka-python with IAM auth"""
import json
from kafka import KafkaProducer
from kafka.errors import KafkaError

class MSKWriter:
    def __init__(self, bootstrap_servers, topic, client_id='oem-consumer-lambda'):
        self.topic = topic
        self.producer = None
        self.client_id = client_id
        self.bootstrap_servers = bootstrap_servers.split(',') if isinstance(bootstrap_servers, str) else bootstrap_servers
        
    def _get_producer(self):
        """Lazy initialize producer"""
        if self.producer is None:
            # Use SASL_SSL with AWS_MSK_IAM
            self.producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                security_protocol='SASL_SSL',
                sasl_mechanism='AWS_MSK_IAM',
                sasl_oauth_token_provider=self._token_provider(),
                client_id=self.client_id
            )
        return self.producer
    
    def _token_provider(self):
        """AWS MSK IAM token provider"""
        from aws_msk_iam_sasl_signer import MSKAuthTokenProvider
        
        class TokenProvider:
            def token(self):
                token, _ = MSKAuthTokenProvider.generate_auth_token('us-east-1')
                return token
        
        return TokenProvider()
    
    def write(self, message):
        """Write message to MSK"""
        try:
            producer = self._get_producer()
            future = producer.send(self.topic, message)
            # Wait for send to complete
            record_metadata = future.get(timeout=10)
            return True
        except KafkaError as e:
            print(f"Failed to write to MSK: {e}")
            return False
    
    def flush(self):
        """Flush pending messages"""
        if self.producer:
            self.producer.flush()
    
    def close(self):
        """Close producer"""
        if self.producer:
            self.producer.close()
