#!/bin/bash

# Generate IoT Core compatible SSL certificates for MSK integration
# Based on AWS blog: https://aws.amazon.com/blogs/iot/how-to-integrate-aws-iot-core-with-amazon-msk/

set -e

echo "Generating IoT Core compatible SSL certificates..."

# Clean up any existing files
rm -f *.jks *.pem *.p12 *.crt *.key

# Generate CA private key
openssl genrsa -out ca-key.pem 2048

# Generate CA certificate
openssl req -new -x509 -key ca-key.pem -out ca-cert.pem -days 365 -subj "/C=US/ST=CA/L=San Francisco/O=AWS/OU=IoT/CN=ca"

# Generate server private key
openssl genrsa -out server-key.pem 2048

# Generate server certificate signing request
openssl req -new -key server-key.pem -out server.csr -subj "/C=US/ST=CA/L=San Francisco/O=AWS/OU=IoT/CN=server"

# Generate server certificate signed by CA
openssl x509 -req -in server.csr -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial -out server-cert.pem -days 365

# Create keystore with server certificate and key
openssl pkcs12 -export -in server-cert.pem -inkey server-key.pem -out server.p12 -name server -password pass:changeit

# Convert PKCS12 to JKS keystore
keytool -importkeystore -deststorepass changeit -destkeypass changeit -destkeystore keystore.jks -srckeystore server.p12 -srcstoretype PKCS12 -srcstorepass changeit -alias server

# Create truststore with CA certificate
keytool -import -alias ca -file ca-cert.pem -keystore truststore.jks -storepass changeit -noprompt

# Encode keystores to base64
KEYSTORE_B64=$(base64 -i keystore.jks)
TRUSTSTORE_B64=$(base64 -i truststore.jks)

# Create JSON for Secrets Manager
cat > ssl-certificates.json << EOF
{
  "keystore": "$KEYSTORE_B64",
  "truststore": "$TRUSTSTORE_B64",
  "keystore_password": "changeit",
  "truststore_password": "changeit"
}
EOF

echo "SSL certificates generated successfully!"
echo "Keystore and truststore created with base64 encoding in ssl-certificates.json"
echo "Upload this to AWS Secrets Manager to update cms-msk-ssl-certificates"

# Clean up intermediate files
rm -f *.pem *.csr *.p12 *.srl

echo "Done!"
