#!/usr/bin/env python3
"""
Test different IoT substitution template formats
"""
import boto3
import json

def test_substitution_formats():
    """Test different substitution template formats"""
    
    # Test different formats
    formats_to_test = [
        "${aws:kafka:cluster:test-cluster:bootstrap-servers:tls}",
        "$${aws:kafka:cluster:test-cluster:bootstrap-servers:tls}",
        "${get(aws.kafka.cluster.test-cluster.bootstrap-servers.tls)}",
        "test-cluster-bootstrap-servers"  # Simple test
    ]
    
    client = boto3.client('iot', region_name='us-east-1')
    
    for i, template in enumerate(formats_to_test):
        print(f"\nTesting format {i+1}: {template}")
        
        rule_payload = {
            "sql": "SELECT * FROM 'test/topic'",
            "description": f"Test rule {i+1}",
            "actions": [
                {
                    "kafka": {
                        "destinationArn": "arn:aws:iot:us-east-1:123456789012:destination/test",
                        "topic": "test-topic", 
                        "key": "${topic(1)}",
                        "clientProperties": {
                            "bootstrap.servers": template,
                            "security.protocol": "SSL"
                        }
                    }
                }
            ]
        }
        
        try:
            client.create_topic_rule(
                ruleName=f'test_rule_{i+1}',
                topicRulePayload=rule_payload
            )
            print(f"✅ Format {i+1} syntax passed!")
            client.delete_topic_rule(ruleName=f'test_rule_{i+1}')
            
        except client.exceptions.InvalidRequestException as e:
            error_msg = str(e)
            if "Unexpected character" in error_msg or "Expected" in error_msg:
                print(f"❌ Format {i+1} syntax error: {error_msg}")
            else:
                print(f"✅ Format {i+1} syntax passed! (Expected error: {error_msg})")
        except Exception as e:
            print(f"❌ Format {i+1} error: {e}")

if __name__ == "__main__":
    test_substitution_formats()
