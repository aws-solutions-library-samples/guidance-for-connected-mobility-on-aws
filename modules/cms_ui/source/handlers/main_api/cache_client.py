"""
API-Compatible DynamoDB Cache Client
Transparent caching layer that maintains DynamoDB interface
"""

import json
import hashlib
import time
from typing import Dict, Any, Optional
import boto3

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class CacheClient:
    """
    Drop-in replacement for DynamoDB client with Redis caching
    Maintains exact same interface as boto3 DynamoDB client
    """
    
    def __init__(self, dynamodb_client, redis_client, ttl: int = 3600):
        self.dynamodb = dynamodb_client
        self.redis = redis_client
        self.ttl = ttl
        
    def _cache_key(self, table_name: str, operation: str, params: Dict) -> str:
        """Generate consistent cache key"""
        key_data = f"{table_name}:{operation}:{json.dumps(params, sort_keys=True)}"
        return f"ddb:{hashlib.md5(key_data.encode()).hexdigest()}"
    
    def _should_cache_operation(self, operation: str, table_name: str) -> bool:
        """Determine if operation should be cached"""
        # Only cache reads for specific tables
        if operation not in ['get_item', 'query']:
            return False
            
        # Cache vehicle state queries
        if 'telemetry' in table_name.lower():
            return True
            
        return False
    
    def get_item(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB get_item with caching"""
        table_name = kwargs.get('TableName', '')
        
        if self._should_cache_operation('get_item', table_name):
            cache_key = self._cache_key(table_name, 'get_item', kwargs)
            
            try:
                # Try cache first
                cached = self.redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass  # Fall through to DynamoDB
        
        # Get from DynamoDB
        response = self.dynamodb.get_item(**kwargs)
        
        # Cache the response
        if self._should_cache_operation('get_item', table_name):
            try:
                self.redis.setex(cache_key, self.ttl, json.dumps(response, default=str))
            except Exception:
                pass  # Continue without caching
                
        return response
    
    def query(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB query with caching"""
        table_name = kwargs.get('TableName', '')
        
        if self._should_cache_operation('query', table_name):
            cache_key = self._cache_key(table_name, 'query', kwargs)
            
            try:
                # Try cache first
                cached = self.redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass  # Fall through to DynamoDB
        
        # Get from DynamoDB
        response = self.dynamodb.query(**kwargs)
        
        # Cache the response
        if self._should_cache_operation('query', table_name):
            try:
                self.redis.setex(cache_key, self.ttl, json.dumps(response, default=str))
            except Exception:
                pass  # Continue without caching
                
        return response
    
    def put_item(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB put_item - invalidate cache"""
        table_name = kwargs.get('TableName', '')
        
        # Invalidate related cache entries
        if 'telemetry' in table_name.lower():
            try:
                # Simple pattern-based invalidation
                pattern = f"ddb:*{table_name}*"
                keys = self.redis.keys(pattern)
                if keys:
                    self.redis.delete(*keys)
            except Exception:
                pass
        
        return self.dynamodb.put_item(**kwargs)
    
    def scan(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB scan - pass through (usually not cached)"""
        return self.dynamodb.scan(**kwargs)
    
    def update_item(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB update_item - invalidate cache"""
        table_name = kwargs.get('TableName', '')
        
        # Invalidate cache
        if self._should_cache_operation('get_item', table_name):
            try:
                pattern = f"ddb:*{table_name}*"
                keys = self.redis.keys(pattern)
                if keys:
                    self.redis.delete(*keys)
            except Exception:
                pass
        
        return self.dynamodb.update_item(**kwargs)
    
    def delete_item(self, **kwargs) -> Dict[str, Any]:
        """DynamoDB delete_item - invalidate cache"""
        table_name = kwargs.get('TableName', '')
        
        # Invalidate cache
        if self._should_cache_operation('get_item', table_name):
            try:
                pattern = f"ddb:*{table_name}*"
                keys = self.redis.keys(pattern)
                if keys:
                    self.redis.delete(*keys)
            except Exception:
                pass
        
        return self.dynamodb.delete_item(**kwargs)


def create_cached_dynamodb_client(redis_endpoint: Optional[str] = None) -> Any:
    """
    Factory function to create cached DynamoDB client
    Falls back to regular DynamoDB if Redis unavailable
    """
    dynamodb_client = boto3.client('dynamodb')
    
    if not redis_endpoint or not REDIS_AVAILABLE:
        print("Redis not available or not configured, using DynamoDB only")
        return dynamodb_client
    
    try:
        redis_client = redis.Redis(
            host=redis_endpoint,
            port=6379,
            decode_responses=True,
            socket_timeout=1,  # Fast timeout
            socket_connect_timeout=1
        )
        
        # Test connection
        redis_client.ping()
        
        return CacheClient(dynamodb_client, redis_client, ttl=300)  # 5 min TTL
        
    except Exception as e:
        print(f"Redis unavailable, using DynamoDB only: {e}")
        return dynamodb_client
