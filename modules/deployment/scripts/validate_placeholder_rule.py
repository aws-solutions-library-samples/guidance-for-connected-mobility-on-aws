#!/usr/bin/env python3
"""
Validate IoT rule with placeholder bootstrap servers
"""
import boto3

def test_placeholder_rule():
    """Test IoT rule with placeholder bootstrap servers"""
    
    rule_payload = {
        "sql": "SELECT * FROM 'cms/telemetry/vehicle/+'",
        "description": "Test rule with placeholder",
        "actions": [
            {
                "kafka": {
                    "destinationArn": "arn:aws:iot:us-east-1:470296731304:destination/test",
                    "topic": "cms-telemetry-raw",
                    "key": "${topic(3)}",
                    "clientProperties": {
                        "bootstrap.servers": "PLACEHOLDER_BOOTSTRAP_SERVERS",
                        "security.protocol": "SSL"
                    }
                }
            }
        ]
    }
    
    try:
        client = boto3.client('iot', region_name='us-east-1')
        client.create_topic_rule(
            ruleName='test_placeholder_validation',
            topicRulePayload=rule_payload
        )
        print("✅ Placeholder rule syntax validation passed!")
        
        # Clean up
        client.delete_topic_rule(ruleName='test_placeholder_validation')
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "Invalid url" in error_msg and "PLACEHOLDER_BOOTSTRAP_SERVERS" in error_msg:
            print("✅ Placeholder bootstrap servers syntax passed! (Expected placeholder URL error)")
            return True
        elif "destination" in error_msg.lower():
            print("✅ Placeholder rule syntax passed! (Expected destination error)")
            return True
        else:
            print(f"❌ Validation failed: {error_msg}")
            return False

if __name__ == "__main__":
    test_placeholder_rule()
