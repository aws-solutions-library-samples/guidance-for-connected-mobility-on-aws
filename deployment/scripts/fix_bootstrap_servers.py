#!/usr/bin/env python3
"""
Fix bootstrap servers in telemetry integration stack
"""

def fix_bootstrap_servers():
    file_path = "./stacks/telemetry_integration_stack.py"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Fix bootstrap servers
    old_bootstrap = '"bootstrap.servers": "${get_secret(\'bootstrap_servers\', \'RoleArn\', \'" + self.telemetry_iot_role.role_arn + "\')}\",'
    new_bootstrap = '"bootstrap.servers": msk_bootstrap_servers,'
    
    content = content.replace(old_bootstrap, new_bootstrap)
    
    # Fix secret references
    old_username = '"sasl.scram.username": "${get_secret(\'username\', \'RoleArn\', \'" + self.telemetry_iot_role.role_arn + "\')}\",'
    new_username = '"sasl.scram.username": "${get_secret(\'" + msk_secret_arn + "\', \'SecretString\', \'username\', \'RoleArn\', \'" + self.telemetry_iot_role.role_arn + "\')}\",'
    
    old_password = '"sasl.scram.password": "${get_secret(\'password\', \'RoleArn\', \'" + self.telemetry_iot_role.role_arn + "\')}\"'
    new_password = '"sasl.scram.password": "${get_secret(\'" + msk_secret_arn + "\', \'SecretString\', \'password\', \'RoleArn\', \'" + self.telemetry_iot_role.role_arn + "\')}\"'
    
    content = content.replace(old_username, new_username)
    content = content.replace(old_password, new_password)
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print("✅ Fixed bootstrap servers and secret references")

if __name__ == "__main__":
    fix_bootstrap_servers()
