#!/usr/bin/env python3
"""
Post-deployment SSL setup for telemetry pipeline
Run after CDK deployment to populate SSL certificates
"""

import boto3
import json
import base64
import subprocess
import tempfile
import sys
import os

def setup_ssl_certificates(secret_name):
    """Generate and store SSL certificates in the specified secret"""
    
    # Use environment variable or default profile
    profile_name = os.environ.get('AWS_PROFILE', 'default')
    session = boto3.Session(profile_name=profile_name)
    secrets_client = session.client('secretsmanager', region_name='us-east-1')
    
    print(f"🔐 Setting up SSL certificates for secret: {secret_name}")
    
    # Generate SSL certificates
    with tempfile.TemporaryDirectory() as temp_dir:
        print("📜 Generating SSL certificates...")
        
        # Generate private key
        subprocess.run([
            'openssl', 'genrsa', '-out', f'{temp_dir}/kafka-client.key', '2048'
        ], check=True, capture_output=True)
        
        # Generate certificate
        subprocess.run([
            'openssl', 'req', '-new', '-x509', '-key', f'{temp_dir}/kafka-client.key',
            '-out', f'{temp_dir}/kafka-client.crt', '-days', '365',
            '-subj', '/C=US/ST=CA/L=SF/O=CMS/CN=kafka-client'
        ], check=True, capture_output=True)
        
        # Create PKCS12 keystore
        subprocess.run([
            'openssl', 'pkcs12', '-export',
            '-in', f'{temp_dir}/kafka-client.crt',
            '-inkey', f'{temp_dir}/kafka-client.key',
            '-out', f'{temp_dir}/kafka-client.p12',
            '-name', 'kafka-client',
            '-password', 'pass:changeit'
        ], check=True, capture_output=True)
        
        # Read and encode certificates
        with open(f'{temp_dir}/kafka-client.p12', 'rb') as f:
            keystore_data = base64.b64encode(f.read()).decode('utf-8')
            
        with open(f'{temp_dir}/kafka-client.crt', 'rb') as f:
            truststore_data = base64.b64encode(f.read()).decode('utf-8')
    
    # Update secret with real certificates
    ssl_data = {
        'keystore': keystore_data,
        'truststore': truststore_data,
        'keystore_password': 'changeit',
        'truststore_password': 'changeit'
    }
    
    try:
        secrets_client.update_secret(
            SecretId=secret_name,
            SecretString=json.dumps(ssl_data)
        )
        print(f"✅ Updated SSL secret: {secret_name}")
        
    except Exception as e:
        print(f"❌ Failed to update SSL secret: {e}")
        return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 post_deploy_ssl_setup.py <secret-name>")
        sys.exit(1)
    
    secret_name = sys.argv[1]
    
    # Check if required tools are available
    try:
        subprocess.run(['openssl', 'version'], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ OpenSSL not found. Please install OpenSSL")
        sys.exit(1)
    
    success = setup_ssl_certificates(secret_name)
    sys.exit(0 if success else 1)
