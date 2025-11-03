"""
gRPC consumer for Ford FCS Feed Service
Consumes from gRPC stream and writes to Kafka
"""
import grpc
import json
import time
import requests
import threading
from typing import Dict, Any, List
from google.protobuf import any_pb2
from google.protobuf.json_format import MessageToDict, MessageToJson

# Import Ford FCS protos (generated from .proto files)
try:
    from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc
    from autonomic.ext.telemetry import metric_pb2
    from autonomic.ext.event import event_pb2
    PROTOS_AVAILABLE = True
except ImportError as e:
    print(f"⚠ Proto files not generated. Run: bash generate_protos.sh")
    print(f"   Import error: {e}")
    PROTOS_AVAILABLE = False

# Import vehicle registration
try:
    from vehicle_registration import VehicleRegistration
    VEHICLE_REGISTRATION_ENABLED = True
except ImportError:
    print("⚠ Vehicle registration disabled (vehicle_registration.py not found)")
    VEHICLE_REGISTRATION_ENABLED = False

class GRPCConsumer:
    def __init__(self, config: Dict[str, Any], kafka_writer, assigned_shards: List[int]):
        self.endpoint = config['endpoint']
        self.flow_name = config['flow_name']
        self.auth_config = config['auth']
        self.kafka_writer = kafka_writer
        self.assigned_shards = assigned_shards
        
        self.access_token = None
        self.token_expiry = 0
        
        # Initialize vehicle registration
        if VEHICLE_REGISTRATION_ENABLED:
            self.vehicle_registration = VehicleRegistration()
            print("✓ Vehicle auto-registration enabled")
        else:
            self.vehicle_registration = None
        
        print(f"✓ gRPC consumer initialized for {self.endpoint}")
        print(f"✓ Flow: {self.flow_name}")
        print(f"✓ Assigned shards: {self.assigned_shards}")
    
    def get_access_token(self) -> str:
        """Get OAuth 2.0 access token from Ford"""
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token
        
        try:
            token_endpoint = self.auth_config['token_endpoint']
            client_id = self.auth_config['client_id']
            client_secret = self.auth_config['client_secret']
            resource_id = self.auth_config.get('resource_id', '')
            
            payload = {
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret
            }
            
            # Only add resource if provided
            if resource_id:
                payload['resource'] = resource_id
            
            print(f"🔐 Requesting OAuth token from {token_endpoint}")
            response = requests.post(token_endpoint, data=payload)
            
            if response.status_code != 200:
                print(f"✗ OAuth error: {response.status_code}")
                print(f"   Response: {response.text}")
            
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            self.token_expiry = time.time() + token_data.get('expires_in', 3600) - 300
            
            print("✓ OAuth token refreshed")
            return self.access_token
        except Exception as e:
            print(f"✗ Failed to get access token: {e}")
            raise
    
    def consume(self):
        """Consume from Ford FCS gRPC stream"""
        print(f"🚀 Starting Ford FCS consumer")
        
        # First, get flow info to see all shards
        try:
            channel = self._create_channel()
            stub = consumer_pb2_grpc.ConsumerStub(channel)
            flow_request = consumer_pb2.GetFlowRequest(flow=self.flow_name)
            flow_response = stub.GetFlow(flow_request)
            
            print(f"\n📊 Flow Summary:")
            print(f"   Total shards: {len(flow_response.shards)}")
            print(f"   Total messages: {flow_response.total_messages}")
            print(f"\n   Shard breakdown:")
            non_empty_shards = []
            for i, shard in enumerate(flow_response.shards):
                if shard.messages > 0 and i in self.assigned_shards:
                    print(f"      Shard {i}: {shard.messages} messages")
                    non_empty_shards.append(i)
            print()
            channel.close()
        except Exception as e:
            print(f"⚠ Could not get flow info: {e}")
            non_empty_shards = self.assigned_shards
        
        # Start a thread for each non-empty shard
        threads = []
        for shard_id in non_empty_shards[:3]:  # Limit to first 3 shards for testing
            thread = threading.Thread(
                target=self._consume_shard_thread,
                args=(shard_id,),
                daemon=True
            )
            thread.start()
            threads.append(thread)
            print(f"✓ Started thread for shard {shard_id}")
        
        # Wait for all threads
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            print("\n⏹ Shutting down gracefully...")
    
    def _consume_shard_thread(self, shard_index: int):
        """Thread wrapper for consuming a shard"""
        while True:
            try:
                self._consume_shard(shard_index)
            except Exception as e:
                print(f"✗ Error on shard {shard_index}: {e}")
                print(f"⏳ Shard {shard_index} reconnecting in 10 seconds...")
                time.sleep(10)
    
    def _create_channel(self):
        """Create a new gRPC channel with OAuth credentials"""
        token = self.get_access_token()
        
        # Create gRPC channel with OAuth credentials
        credentials = grpc.access_token_call_credentials(token)
        channel_credentials = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            credentials
        )
        
        # Remove https:// prefix for gRPC channel
        endpoint = self.endpoint.replace('https://', '').replace('http://', '')
        channel = grpc.secure_channel(endpoint, channel_credentials)
        return channel
    
    def _consume_shard(self, shard_index: int):
        """Consume from a specific shard with dedicated channel"""
        print(f"📡 Consuming shard {shard_index} from flow {self.flow_name}")
        
        try:
            # Create dedicated channel for this shard (required for feed.autonomic.ai)
            channel = self._create_channel()
            stub = consumer_pb2_grpc.ConsumerStub(channel)
            print(f"✓ Connected to {self.endpoint}")
            
            # Get flow info to get actual shard IDs
            flow_request = consumer_pb2.GetFlowRequest(flow=self.flow_name)
            flow_response = stub.GetFlow(flow_request)
            
            print(f"✓ Flow has {len(flow_response.shards)} shards, {flow_response.total_messages} total messages")
            
            if shard_index >= len(flow_response.shards):
                print(f"⚠ Shard index {shard_index} out of range (only {len(flow_response.shards)} shards)")
                return
            
            shard_id = flow_response.shards[shard_index].id
            shard_info = flow_response.shards[shard_index]
            print(f"✓ Shard {shard_index} has {shard_info.messages} messages")
            
            if shard_info.messages == 0:
                print(f"⚠ Shard {shard_index} is empty, skipping")
                channel.close()
                return
            
            # Get start reference for this shard (start from earliest)
            ref_request = consumer_pb2.GetStartReferenceRequest(
                flow=self.flow_name,
                shard=shard_id,
                start_type=consumer_pb2.GetStartReferenceRequest.EARLIEST
            )
            ref_response = stub.GetStartReference(ref_request)
            print(f"✓ Got start reference for shard {shard_index}")
            
            # Create GetEvents request with the reference
            request = consumer_pb2.GetEventsRequest(
                flow=self.flow_name,
                shard=shard_id,
                reference=ref_response.reference,
                count_limit=100,  # Limit to 100 events per request
                timeout=5000  # 5 second timeout
            )
            
            print(f"📥 Starting to read events from shard {shard_index}...")
            
            # Stream messages from shard
            message_count = 0
            empty_response_count = 0
            max_empty_responses = 5  # Stop after 5 consecutive empty responses
            
            for response in stub.GetEvents(request):
                if len(response.events) == 0:
                    empty_response_count += 1
                    print(f"✓ Received response with 0 events (empty count: {empty_response_count}/{max_empty_responses})")
                    
                    if empty_response_count >= max_empty_responses:
                        print(f"✓ Shard {shard_index} exhausted (no new data), moving on")
                        break
                else:
                    empty_response_count = 0  # Reset counter when we get data
                    print(f"✓ Received response with {len(response.events)} events")
                    
                for feed_event in response.events:
                    self._process_feed_event(feed_event)
                    message_count += 1
                    
                    if message_count % 100 == 0:
                        print(f"✓ Processed {message_count} messages from shard {shard_id}")
            
            print(f"✓ Finished shard {shard_index}: processed {message_count} total messages")
            channel.close()
                
        except grpc.RpcError as e:
            print(f"✗ gRPC error on shard {shard_id}: {e.code()} - {e.details()}")
            raise
        except Exception as e:
            print(f"✗ Error consuming shard {shard_id}: {e}")
            raise
    
    def _process_feed_event(self, feed_event):
        """Process a FeedEvent from Ford FCS"""
        try:
            # Extract vehicle ID from shard key
            vehicle_id = feed_event.shard_key
            
            # Unpack the typed data (Any proto)
            typed_data = feed_event.typed_data
            
            # Check if it's a Metric or Event
            if typed_data.Is(metric_pb2.Metric.DESCRIPTOR):
                metric = metric_pb2.Metric()
                typed_data.Unpack(metric)
                self._handle_metric(vehicle_id, feed_event, metric)
                
            elif typed_data.Is(event_pb2.Event.DESCRIPTOR):
                event = event_pb2.Event()
                typed_data.Unpack(event)
                self._handle_event(vehicle_id, feed_event, event)
            else:
                print(f"⚠ Unknown message type: {typed_data.type_url}")
                
        except Exception as e:
            import traceback
            print(f"✗ Error processing feed event: {e}")
            print(f"✗ Traceback: {traceback.format_exc()}")
    
    def _convert_bytes_to_str(self, obj):
        """Recursively convert bytes to strings in dict/list"""
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='ignore')
        elif isinstance(obj, dict):
            return {k: self._convert_bytes_to_str(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_bytes_to_str(item) for item in obj]
        return obj
    
    def _handle_metric(self, vehicle_id: str, feed_event, metric):
        """Handle a Metric message"""
        # Auto-register vehicle if needed
        if self.vehicle_registration:
            asset_info = MessageToDict(feed_event.asset_info)
            self.vehicle_registration.register_vehicle_if_new(vehicle_id, 'ford', asset_info)
        
        # Convert entire protobuf to JSON string then parse back
        metric_json = json.loads(MessageToJson(metric, preserving_proto_field_name=True))
        asset_json = json.loads(MessageToJson(feed_event.asset_info, preserving_proto_field_name=True))
        
        message_dict = {
            'oem_source': 'ford',
            'reference': feed_event.reference.hex() if isinstance(feed_event.reference, bytes) else str(feed_event.reference),
            'timestamp': feed_event.timestamp.ToJsonString(),
            'shardKey': feed_event.shard_key,
            'assetInfo': asset_json,
            'typedData': {'@type': 'type.googleapis.com/autonomic.ext.telemetry.Metric', **metric_json}
        }
        
        success = self.kafka_writer.write(message_dict); print(f"✓ MSK write: {success}")
    
    def _handle_event(self, vehicle_id: str, feed_event, event):
        """Handle an Event message"""
        event_json = json.loads(MessageToJson(event, preserving_proto_field_name=True))
        asset_json = json.loads(MessageToJson(feed_event.asset_info, preserving_proto_field_name=True))
        
        message_dict = {
            'oem_source': 'ford',
            'reference': feed_event.reference.hex() if isinstance(feed_event.reference, bytes) else str(feed_event.reference),
            'timestamp': feed_event.timestamp.ToJsonString(),
            'shardKey': feed_event.shard_key,
            'assetInfo': asset_json,
            'typedData': {'@type': 'type.googleapis.com/autonomic.ext.event.Event', **event_json}
        }
        
        success = self.kafka_writer.write(message_dict); print(f"✓ MSK write event: {success}")
