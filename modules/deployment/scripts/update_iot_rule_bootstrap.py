#!/usr/bin/env python3
"""
Update IoT rule with actual MSK bootstrap servers
"""
import boto3
import sys
import json

def update_iot_rule_bootstrap(rule_name, bootstrap_servers, profile=None):
    """Update IoT rule with actual bootstrap servers"""
    
    try:
        # Create IoT client
        session = boto3.Session(profile_name=profile if profile else None)
        iot_client = session.client('iot', region_name='us-east-1')
        
        print(f"🔍 Getting current IoT rule: {rule_name}")
        
        # Get current rule
        response = iot_client.get_topic_rule(ruleName=rule_name)
        rule_payload = response['rule']
        
        # Update bootstrap servers in client properties
        for action in rule_payload['actions']:
            if 'kafka' in action:
                kafka_action = action['kafka']
                if 'clientProperties' in kafka_action:
                    client_props = kafka_action['clientProperties']
                    if 'bootstrap.servers' in client_props:
                        old_value = client_props['bootstrap.servers']
                        client_props['bootstrap.servers'] = bootstrap_servers
                        print(f"📡 Updated bootstrap.servers:")
                        print(f"   From: {old_value}")
                        print(f"   To: {bootstrap_servers}")
        
        # Update the rule
        iot_client.replace_topic_rule(
            ruleName=rule_name,
            topicRulePayload={
                'sql': rule_payload['sql'],
                'description': rule_payload['description'],
                'actions': rule_payload['actions'],
                'ruleDisabled': rule_payload.get('ruleDisabled', False),
                'awsIotSqlVersion': rule_payload.get('awsIotSqlVersion', '2016-03-23')
            }
        )
        
        print("✅ IoT rule updated successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error updating IoT rule: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 update_iot_rule_bootstrap.py <rule_name> <bootstrap_servers> [profile]")
        sys.exit(1)
    
    rule_name = sys.argv[1]
    bootstrap_servers = sys.argv[2]
    profile = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    
    success = update_iot_rule_bootstrap(rule_name, bootstrap_servers, profile)
    sys.exit(0 if success else 1)
