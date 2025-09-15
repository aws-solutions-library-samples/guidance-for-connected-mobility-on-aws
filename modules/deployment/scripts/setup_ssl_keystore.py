#!/usr/bin/env python3
"""
Setup SSL keystore and truststore for MSK IoT integration
"""

import os
import boto3
import json
import base64
import subprocess
import tempfile
import os

def create_ssl_certificates():
    """Generate SSL certificates for MSK"""
    
    # Create temporary directory for certificates
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Working in: {temp_dir}")
        
        # Generate private key
        subprocess.run([
            'openssl', 'genrsa', '-out', f'{temp_dir}/kafka-client.key', '2048'
        ], check=True)
        
        # Generate certificate signing request
        subprocess.run([
            'openssl', 'req', '-new', '-key', f'{temp_dir}/kafka-client.key',
            '-out', f'{temp_dir}/kafka-client.csr',
            '-subj', '/C=US/ST=CA/L=SF/O=CMS/CN=kafka-client'
        ], check=True)
        
        # Generate self-signed certificate
        subprocess.run([
            'openssl', 'x509', '-req', '-days', '365',
            '-in', f'{temp_dir}/kafka-client.csr',
            '-signkey', f'{temp_dir}/kafka-client.key',
            '-out', f'{temp_dir}/kafka-client.crt'
        ], check=True)
        
        # Create PKCS12 keystore
        subprocess.run([
            'openssl', 'pkcs12', '-export',
            '-in', f'{temp_dir}/kafka-client.crt',
            '-inkey', f'{temp_dir}/kafka-client.key',
            '-out', f'{temp_dir}/kafka-client.p12',
            '-name', 'kafka-client',
            '-password', 'pass:changeit'
        ], check=True)
        
        # Convert to JKS keystore
        subprocess.run([
            'keytool', '-importkeystore',
            '-deststorepass', 'changeit',
            '-destkeypass', 'changeit',
            '-destkeystore', f'{temp_dir}/kafka-client.jks',
            '-srckeystore', f'{temp_dir}/kafka-client.p12',
            '-srcstoretype', 'PKCS12',
            '-srcstorepass', 'changeit',
            '-alias', 'kafka-client',
            '-noprompt'
        ], check=True)
        
        # Create truststore (using same cert for simplicity)
        subprocess.run([
            'keytool', '-import',
            '-trustcacerts',
            '-alias', 'kafka-ca',
            '-file', f'{temp_dir}/kafka-client.crt',
            '-keystore', f'{temp_dir}/kafka-truststore.jks',
            '-storepass', 'changeit',
            '-noprompt'
        ], check=True)
        
        # Read and encode certificates
        with open(f'{temp_dir}/kafka-client.jks', 'rb') as f:
            keystore_data = base64.b64encode(f.read()).decode('utf-8')
            
        with open(f'{temp_dir}/kafka-truststore.jks', 'rb') as f:
            truststore_data = base64.b64encode(f.read()).decode('utf-8')
        
        return {
            'keystore': keystore_data,
            'truststore': truststore_data,
            'keystore_password': 'changeit',
            'truststore_password': 'changeit'
        }

def create_ssl_secret(ssl_data):
    """Create Secrets Manager secret with SSL certificates"""
    
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    secrets_client = session.client('secretsmanager', region_name='us-east-1')
    
    secret_name = 'cms-msk-ssl-certificates'
    
    try:
        # Delete existing secret if it exists
        try:
            secrets_client.delete_secret(
                SecretId=secret_name,
                ForceDeleteWithoutRecovery=True
            )
            print(f"🗑️ Deleted existing secret: {secret_name}")
        except secrets_client.exceptions.ResourceNotFoundException:
            pass
        
        # Create new secret
        response = secrets_client.create_secret(
            Name=secret_name,
            Description='SSL certificates for MSK IoT Core integration',
            SecretString=json.dumps(ssl_data)
        )
        
        print(f"✅ Created SSL secret: {response['ARN']}")
        return response['ARN']
        
    except Exception as e:
        print(f"❌ Failed to create SSL secret: {e}")
        return None

def update_iot_role_permissions(secret_arn):
    """Add Secrets Manager permissions to IoT role"""
    
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    iam_client = session.client('iam', region_name='us-east-1')
    
    role_name = 'IoTMSKVPCRole'
    
    # Add Secrets Manager permissions
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                "Resource": secret_arn
            }
        ]
    }
    
    try:
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName='SecretsManagerAccess',
            PolicyDocument=json.dumps(policy_document)
        )
        print(f"✅ Added Secrets Manager permissions to {role_name}")
    except Exception as e:
        print(f"❌ Failed to update role permissions: {e}")

def main():
    """Main function to setup SSL certificates"""
    
    print("🔐 Setting up SSL certificates for MSK IoT integration...")
    
    # Check if required tools are available
    try:
        subprocess.run(['openssl', 'version'], check=True, capture_output=True)
        subprocess.run(['keytool', '-help'], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Required tools not found. Please install OpenSSL and Java keytool")
        return
    
    # Generate SSL certificates
    print("📜 Generating SSL certificates...")
    ssl_data = create_ssl_certificates()
    print("✅ SSL certificates generated")
    
    # Create Secrets Manager secret
    print("🔒 Creating Secrets Manager secret...")
    secret_arn = create_ssl_secret(ssl_data)
    
    if secret_arn:
        # Update IoT role permissions
        print("🔑 Updating IoT role permissions...")
        update_iot_role_permissions(secret_arn)
        
        print("\n🎉 SSL setup completed!")
        print(f"Secret ARN: {secret_arn}")
        print("You can now create the IoT rule with SSL configuration")
    else:
        print("❌ SSL setup failed")

if __name__ == "__main__":
    main()
