#!/usr/bin/env python3
"""
Test correct MSK substitution template format
"""
import boto3

def test_msk_substitution():
    """Test the correct MSK substitution format"""
    
    # According to AWS docs, the correct format for MSK is:
    formats_to_test = [
        "${aws:kafka:cluster:test-cluster:bootstrap-servers:tls}",  # Original
        "${get(aws.kafka.cluster.test-cluster.bootstrap-servers.tls)}",  # Alternative
        "${aws.kafka.cluster.test-cluster.bootstrap-servers.tls}",  # Without colon prefix
    ]
    
    client = boto3.client('iot', region_name='us-east-1')
    
    for i, template in enumerate(formats_to_test):
        print(f"\nTesting MSK format {i+1}: {template}")
        
        rule_payload = {
            "sql": "SELECT * FROM 'test/topic'",
            "description": f"MSK test rule {i+1}",
            "actions": [
                {
                    "kafka": {
                        "destinationArn": "arn:aws:iot:us-east-1:123456789012:destination/test",
                        "topic": "test-topic", 
                        "key": "${topic(1)}",
                        "clientProperties": {
                            "bootstrap.servers": template,
                            "security.protocol": "SSL",
                            "ssl.keystore": "dummy",
                            "ssl.keystore.password": "dummy"
                        }
                    }
                }
            ]
        }
        
        try:
            client.create_topic_rule(
                ruleName=f'msk_test_rule_{i+1}',
                topicRulePayload=rule_payload
            )
            print(f"✅ MSK format {i+1} passed!")
            client.delete_topic_rule(ruleName=f'msk_test_rule_{i+1}')
            
        except Exception as e:
            error_msg = str(e)
            if "Invalid url" in error_msg:
                print(f"❌ MSK format {i+1} invalid URL: {error_msg}")
            elif "destination" in error_msg.lower():
                print(f"✅ MSK format {i+1} URL valid! (Expected destination error)")
            else:
                print(f"? MSK format {i+1}: {error_msg}")

if __name__ == "__main__":
    test_msk_substitution()
