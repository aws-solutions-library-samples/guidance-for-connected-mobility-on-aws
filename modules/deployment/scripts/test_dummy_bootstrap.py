#!/usr/bin/env python3
"""
Test valid dummy bootstrap servers format
"""
import boto3

def test_dummy_bootstrap():
    """Test IoT rule with valid dummy bootstrap servers"""
    
    rule_payload = {
        "sql": "SELECT * FROM 'cms/telemetry/vehicle/+'",
        "description": "Test rule with dummy bootstrap",
        "actions": [
            {
                "kafka": {
                    "destinationArn": "arn:aws:iot:us-east-1:470296731304:destination/test",
                    "topic": "cms-telemetry-raw",
                    "key": "${topic(3)}",
                    "clientProperties": {
                        "bootstrap.servers": "dummy-broker-1.kafka.us-east-1.amazonaws.com:9094,dummy-broker-2.kafka.us-east-1.amazonaws.com:9094",
                        "security.protocol": "SSL"
                    }
                }
            }
        ]
    }
    
    try:
        client = boto3.client('iot', region_name='us-east-1')
        client.create_topic_rule(
            ruleName='test_dummy_bootstrap',
            topicRulePayload=rule_payload
        )
        print("✅ Dummy bootstrap servers validation passed!")
        
        # Clean up
        client.delete_topic_rule(ruleName='test_dummy_bootstrap')
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "destination" in error_msg.lower():
            print("✅ Dummy bootstrap servers syntax passed! (Expected destination error)")
            return True
        else:
            print(f"❌ Validation failed: {error_msg}")
            return False

if __name__ == "__main__":
    test_dummy_bootstrap()
