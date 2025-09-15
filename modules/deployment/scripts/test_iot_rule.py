#!/usr/bin/env python3
"""
Test IoT rule syntax validation
"""
import boto3
import json

def test_iot_rule_syntax():
    """Test the IoT rule syntax without creating MSK resources"""
    
    # Test substitution template
    bootstrap_template = "${aws:kafka:cluster:test-cluster:bootstrap-servers:tls}"
    
    # Create a minimal IoT rule for syntax validation
    rule_payload = {
        "sql": "SELECT * FROM 'test/topic'",
        "description": "Test rule for syntax validation",
        "actions": [
            {
                "kafka": {
                    "destinationArn": "arn:aws:iot:us-east-1:123456789012:destination/test",
                    "topic": "test-topic",
                    "key": "${topic(1)}",
                    "clientProperties": {
                        "bootstrap.servers": bootstrap_template,
                        "security.protocol": "SSL"
                    }
                }
            }
        ]
    }
    
    print("Testing IoT rule syntax...")
    print(f"Bootstrap template: {bootstrap_template}")
    print(f"Rule payload: {json.dumps(rule_payload, indent=2)}")
    
    # Try to create the rule (will fail due to missing destination, but syntax will be validated)
    try:
        client = boto3.client('iot', region_name='us-east-1')
        client.create_topic_rule(
            ruleName='test_syntax_validation_rule',
            topicRulePayload=rule_payload
        )
        print("✅ Syntax validation passed!")
        
        # Clean up
        client.delete_topic_rule(ruleName='test_syntax_validation_rule')
        
    except client.exceptions.InvalidRequestException as e:
        error_msg = str(e)
        if "Unexpected character" in error_msg or "Expected '}'" in error_msg:
            print(f"❌ Syntax error: {error_msg}")
            return False
        elif "destination" in error_msg.lower() or "arn" in error_msg.lower():
            print("✅ Syntax validation passed! (Expected destination error)")
            return True
        else:
            print(f"❌ Unexpected error: {error_msg}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    test_iot_rule_syntax()
