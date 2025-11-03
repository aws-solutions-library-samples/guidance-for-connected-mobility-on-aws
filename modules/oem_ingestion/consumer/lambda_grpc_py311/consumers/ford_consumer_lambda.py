"""Ford consumer optimized for Lambda with timeout handling"""
import time
from consumers.ford_consumer import FordConsumer as BaseFordConsumer

class FordConsumerLambda(BaseFordConsumer):
    """Ford consumer with timeout support for Lambda"""
    
    def consume_with_timeout(self, max_seconds):
        """
        Consume messages with timeout
        Yields messages until max_seconds elapsed
        """
        start_time = time.time()
        
        # Get flow info
        flow_name = self.config['flow_name']
        
        # Process first 3 shards (adjust based on load)
        for shard_index in range(min(3, len(self._get_shards()))):
            if time.time() - start_time > max_seconds:
                break
            
            for message in self._consume_shard_with_timeout(
                shard_index, 
                max_seconds - (time.time() - start_time)
            ):
                yield message
    
    def _consume_shard_with_timeout(self, shard_index, timeout_seconds):
        """Consume single shard with timeout"""
        start_time = time.time()
        empty_count = 0
        
        for response in self._stream_events(shard_index):
            # Check timeout
            if time.time() - start_time > timeout_seconds:
                break
            
            if not response.events:
                empty_count += 1
                if empty_count >= 5:  # Shard exhausted
                    break
                continue
            
            empty_count = 0
            for event in response.events:
                yield event
