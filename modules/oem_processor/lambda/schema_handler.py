"""
Generic Schema Handler
Supports multiple encoding formats: Protobuf, JSON Schema, Avro, etc.
Schemas stored in S3 per OEM
"""
import json
import os
import boto3
import tempfile
import subprocess
from pathlib import Path

s3 = boto3.client('s3')

class SchemaHandler:
    """Handles schema loading and message encoding/decoding"""
    
    SUPPORTED_ENCODINGS = ['protobuf', 'json', 'avro', 'raw']
    
    def __init__(self, oem_name, manifest_bucket):
        self.oem_name = oem_name
        self.manifest_bucket = manifest_bucket
        self.encoding_type = None
        self.schema_loaded = False
    
    def load_schema(self, encoding_type):
        """Load schema files from S3 based on encoding type"""
        self.encoding_type = encoding_type
        
        if encoding_type == 'raw':
            # No schema needed
            self.schema_loaded = True
            return True
        
        schema_prefix = f"manifests/{self.oem_name}/schemas/"
        
        try:
            if encoding_type == 'protobuf':
                return self._load_protobuf_schemas(schema_prefix)
            elif encoding_type == 'json':
                return self._load_json_schema(schema_prefix)
            elif encoding_type == 'avro':
                return self._load_avro_schema(schema_prefix)
            else:
                raise ValueError(f"Unsupported encoding: {encoding_type}")
        except Exception as e:
            print(f"Error loading schema: {e}")
            return False
    
    def _load_protobuf_schemas(self, schema_prefix):
        """Download .proto files from S3 and compile them"""
        # List all .proto files
        response = s3.list_objects_v2(
            Bucket=self.manifest_bucket,
            Prefix=schema_prefix
        )
        
        if 'Contents' not in response:
            print(f"No proto files found at {schema_prefix}")
            return False
        
        # Create temp directory for proto files
        proto_dir = tempfile.mkdtemp()
        
        # Download all .proto files maintaining directory structure
        for obj in response['Contents']:
            if obj['Key'].endswith('.proto'):
                # Get relative path
                rel_path = obj['Key'].replace(schema_prefix, '')
                local_path = os.path.join(proto_dir, rel_path)
                
                # Create directories
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # Download file
                s3.download_file(self.manifest_bucket, obj['Key'], local_path)
        
        # Compile proto files to Python
        try:
            self._compile_protos(proto_dir)
            self.schema_loaded = True
            return True
        except Exception as e:
            print(f"Failed to compile protos: {e}")
            return False
    
    def _compile_protos(self, proto_dir):
        """Compile .proto files to Python using protoc"""
        # Find all .proto files
        proto_files = list(Path(proto_dir).rglob('*.proto'))
        
        if not proto_files:
            raise Exception("No .proto files found")
        
        # Run protoc
        cmd = [
            'protoc',
            f'--proto_path={proto_dir}',
            f'--python_out={proto_dir}',
            f'--grpc_python_out={proto_dir}'
        ] + [str(f) for f in proto_files]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"protoc failed: {result.stderr}")
        
        # Add proto_dir to Python path so imports work
        import sys
        sys.path.insert(0, proto_dir)
        
        print(f"✓ Compiled {len(proto_files)} proto files")
    
    def _load_json_schema(self, schema_prefix):
        """Load JSON schema for validation"""
        schema_key = f"{schema_prefix}schema.json"
        
        try:
            response = s3.get_object(Bucket=self.manifest_bucket, Key=schema_key)
            self.json_schema = json.loads(response['Body'].read())
            self.schema_loaded = True
            return True
        except Exception as e:
            print(f"Failed to load JSON schema: {e}")
            return False
    
    def _load_avro_schema(self, schema_prefix):
        """Load Avro schema"""
        schema_key = f"{schema_prefix}schema.avsc"
        
        try:
            response = s3.get_object(Bucket=self.manifest_bucket, Key=schema_key)
            import avro.schema
            self.avro_schema = avro.schema.parse(response['Body'].read().decode('utf-8'))
            self.schema_loaded = True
            return True
        except Exception as e:
            print(f"Failed to load Avro schema: {e}")
            return False
    
    def decode_message(self, raw_data):
        """Decode message based on encoding type"""
        if not self.schema_loaded:
            raise Exception("Schema not loaded")
        
        if self.encoding_type == 'raw':
            return raw_data
        elif self.encoding_type == 'json':
            return json.loads(raw_data)
        elif self.encoding_type == 'protobuf':
            # Protobuf decoding requires knowing the message type
            # This would be specified in the manifest
            raise NotImplementedError("Protobuf decoding requires message type")
        elif self.encoding_type == 'avro':
            import avro.io
            import io
            bytes_reader = io.BytesIO(raw_data)
            decoder = avro.io.BinaryDecoder(bytes_reader)
            reader = avro.io.DatumReader(self.avro_schema)
            return reader.read(decoder)
        
        raise ValueError(f"Unknown encoding: {self.encoding_type}")
