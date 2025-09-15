#!/usr/bin/env python3
"""
Generate Real SSL Certificates for MSK Kafka Integration
This script creates proper SSL certificates for production use
"""

import subprocess
import tempfile
import os
import base64
import json

def generate_ssl_certificates():
    """Generate SSL certificates for Kafka"""
    print("🔐 Generating SSL certificates for MSK Kafka...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create CA private key
        ca_key_path = os.path.join(temp_dir, "ca-key.pem")
        subprocess.run([
            "openssl", "genrsa", "-out", ca_key_path, "2048"
        ], check=True)
        
        # Create CA certificate
        ca_cert_path = os.path.join(temp_dir, "ca-cert.pem")
        subprocess.run([
            "openssl", "req", "-new", "-x509", "-key", ca_key_path,
            "-out", ca_cert_path, "-days", "365",
            "-subj", "/C=US/ST=WA/L=Seattle/O=FleetTelemetry/CN=kafka-ca"
        ], check=True)
        
        # Create client private key
        client_key_path = os.path.join(temp_dir, "client-key.pem")
        subprocess.run([
            "openssl", "genrsa", "-out", client_key_path, "2048"
        ], check=True)
        
        # Create client certificate signing request
        client_csr_path = os.path.join(temp_dir, "client.csr")
        subprocess.run([
            "openssl", "req", "-new", "-key", client_key_path,
            "-out", client_csr_path,
            "-subj", "/C=US/ST=WA/L=Seattle/O=FleetTelemetry/CN=iot-client"
        ], check=True)
        
        # Sign client certificate
        client_cert_path = os.path.join(temp_dir, "client-cert.pem")
        subprocess.run([
            "openssl", "x509", "-req", "-in", client_csr_path,
            "-CA", ca_cert_path, "-CAkey", ca_key_path,
            "-CAcreateserial", "-out", client_cert_path, "-days", "365"
        ], check=True)
        
        # Create PKCS12 keystore
        keystore_path = os.path.join(temp_dir, "kafka-client.keystore.p12")
        subprocess.run([
            "openssl", "pkcs12", "-export", "-in", client_cert_path,
            "-inkey", client_key_path, "-out", keystore_path,
            "-name", "kafka-client", "-password", "pass:changeit"
        ], check=True)
        
        # Create truststore
        truststore_path = os.path.join(temp_dir, "kafka-client.truststore.p12")
        subprocess.run([
            "keytool", "-import", "-file", ca_cert_path,
            "-alias", "ca-cert", "-keystore", truststore_path,
            "-storepass", "changeit", "-noprompt", "-storetype", "PKCS12"
        ], check=True)
        
        # Read files as base64
        with open(keystore_path, 'rb') as f:
            keystore_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        with open(truststore_path, 'rb') as f:
            truststore_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        print("✅ SSL certificates generated successfully")
        
        return {
            'keystore': keystore_b64,
            'truststore': truststore_b64,
            'keystore_password': 'changeit',
            'truststore_password': 'changeit'
        }

def update_cdk_stack_with_real_certificates():
    """Update CDK stack with real certificates"""
    print("🔄 Generating certificates for CDK stack...")
    
    certs = generate_ssl_certificates()
    
    # Create a JSON file with the certificates
    cert_file = "/path/to/workspace/lib/ssl_certificates.json"
    with open(cert_file, 'w') as f:
        json.dump(certs, f, indent=2)
    
    print(f"✅ Certificates saved to: {cert_file}")
    print("🔧 Update your CDK stack to use these real certificates")
    print("")
    print("📋 To use in CDK:")
    print("   1. Load certificates from ssl_certificates.json")
    print("   2. Replace placeholder values in _create_ssl_certificates()")
    print("   3. Deploy updated stack")
    
    return cert_file

if __name__ == "__main__":
    cert_file = update_cdk_stack_with_real_certificates()
    print(f"\n🎯 Certificates ready for production deployment!")
    print(f"📁 File: {cert_file}")
