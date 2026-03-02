#!/usr/bin/env python3
"""
Complete Integration Script - IoT rules + Flink configuration + Start applications
"""

import boto3
import json
import os
import time

def get_stack_outputs(stack_name: str):
    cf = boto3.client('cloudformation')
    try:
        response = cf.describe_stacks(StackName=stack_name)
        outputs = {}
        if 'Outputs' in response['Stacks'][0]:
            for output in response['Stacks'][0]['Outputs']:
                outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except Exception as e:
        print(f"Error getting stack outputs: {e}")
        return {}

def get_msk_bootstrap_servers_scram(cluster_arn: str) -> str:
    """Get SCRAM bootstrap servers (port 9096)"""
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        # Use SCRAM servers (port 9096) for IoT rule
        scram_servers = response.get('BootstrapBrokerStringSaslScram')
        if scram_servers:
            return scram_servers
        # Fallback: convert IAM servers to SCRAM by changing port
        iam_servers = response.get('BootstrapBrokerStringSaslIam', '')
        return iam_servers.replace(':9098', ':9096')
    except Exception as e:
        print(f"Error getting SCRAM bootstrap servers: {e}")
        return None

def get_msk_bootstrap_servers(cluster_arn: str) -> str:
    kafka = boto3.client('kafka')
    try:
        response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        # Use IAM servers (port 9098) instead of SCRAM servers (port 9096)
        return response.get('BootstrapBrokerStringSaslIam', response.get('BootstrapBrokerString'))
    except Exception as e:
        print(f"Error getting bootstrap servers: {e}")
        return None

def create_vpc_destination_if_needed(cluster_arn: str, role_arn: str):
    """Get existing VPC destination or create new one"""
    iot = boto3.client('iot')
    
    # Check existing destinations first
    destinations = iot.list_topic_rule_destinations()
    for dest in destinations.get('destinationSummaries', []):
        if dest.get('status') in ['ENABLED', 'IN_PROGRESS']:
            print(f"Using existing VPC destination: {dest['arn']} (status: {dest['status']})")
            return dest['arn']
    
    print("No existing VPC destination found, creating new one...")
    # Only create if none exists
    ec2 = boto3.client('ec2')
    
    try:
        # Get MSK VPC info from stack outputs
        cf = boto3.client('cloudformation')
        deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        msk_stack_name = f"cms-{deployment_stage}-msk"
        
        try:
            msk_outputs = cf.describe_stacks(StackName=msk_stack_name)['Stacks'][0]['Outputs']
            msk_outputs_dict = {output['OutputKey']: output['OutputValue'] for output in msk_outputs}
            
            vpc_id = msk_outputs_dict.get('MSKVpcId')
            # Use public subnets for VPC destination (IoT needs internet access)
            subnet_ids = msk_outputs_dict.get('MSKPublicSubnetIds', '').split(',')
            sg_id = msk_outputs_dict.get('MSKSecurityGroupId')
            
            print(f"🔍 Using MSK VPC: {vpc_id}")
            print(f"🔍 Using {len(subnet_ids)} public subnets for VPC destination: {subnet_ids}")
            print(f"🔍 Using security group: {sg_id}")
            
            if not vpc_id or not subnet_ids or not sg_id:
                raise Exception("Missing MSK VPC configuration in stack outputs")
                
        except Exception as e:
            print(f"⚠️  Could not get MSK VPC info, falling back to default VPC: {e}")
            # Fallback to default VPC
            vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
            if not vpcs['Vpcs']:
                raise Exception("No default VPC found")
            
            vpc_id = vpcs['Vpcs'][0]['VpcId']
            subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
            subnet_ids = [s['SubnetId'] for s in subnets['Subnets']]  # Use ALL subnets
            print(f"🔍 Using {len(subnet_ids)} subnets for VPC destination: {subnet_ids}")
            
            if len(subnet_ids) < 2:
                raise Exception(f"Need at least 2 subnets, found {len(subnet_ids)}")
            
            # Get default security group
            default_sgs = ec2.describe_security_groups(Filters=[
                {'Name': 'group-name', 'Values': ['default']},
                {'Name': 'vpc-id', 'Values': [vpc_id]}
            ])
            sg_ids = [sg['GroupId'] for sg in default_sgs['SecurityGroups'][:1]]
            sg_id = sg_ids[0] if sg_ids else None
        
        # Create VPC destination
        response = iot.create_topic_rule_destination(
            destinationConfiguration={
                'vpcConfiguration': {
                    'vpcId': vpc_id,
                    'subnetIds': subnet_ids,
                    'securityGroups': [sg_id],
                    'roleArn': role_arn
                }
            }
        )
        
        print(f"🔍 VPC destination response: {response}")
        destination_arn = response.get('destinationArn') or response.get('DestinationArn')
        if not destination_arn:
            # Try alternative response structure
            destination_arn = response.get('destination', {}).get('arn')
        
        print(f"Created VPC destination: {destination_arn}")
        
        if not destination_arn:
            raise Exception("VPC destination ARN not found in response")
            
        return destination_arn
        
    except Exception as e:
        print(f"Error creating VPC destination: {e}")
        raise

def create_iot_rule_with_scram(rule_name: str, cluster_arn: str, bootstrap_servers: str, secret_arn: str, role_arn: str, deployment_stage: str):
    """Create IoT rule with SCRAM authentication"""
    iot = boto3.client('iot')
    
    try:
        # Get or create VPC destination
        destination_arn = create_vpc_destination_if_needed(cluster_arn, role_arn)
        
        # Delete existing rule if it exists
        try:
            iot.get_topic_rule(ruleName=rule_name)
            print(f"Deleting existing rule: {rule_name}")
            iot.delete_topic_rule(ruleName=rule_name)
            time.sleep(2)
        except:
            pass
        
        # Create rule with SCRAM credentials from Secrets Manager
        rule_payload = {
            'sql': "SELECT * FROM 'telemetry/+'",
            'description': 'Route telemetry data to MSK cluster with SCRAM auth',
            'awsIotSqlVersion': '2016-03-23',
            'actions': [
                {
                    'kafka': {
                        'destinationArn': destination_arn,
                        'topic': 'cms-telemetry-raw',
                        'key': 'basic-ingest',
                        'clientProperties': {
                            'acks': '1',
                            'bootstrap.servers': bootstrap_servers,
                            'security.protocol': 'SASL_SSL',
                            'sasl.mechanism': 'SCRAM-SHA-512',
                            'sasl.scram.username': f'${{get_secret("{secret_arn}", "SecretString", "username", "{role_arn}")}}',
                            'sasl.scram.password': f'${{get_secret("{secret_arn}", "SecretString", "password", "{role_arn}")}}'
                        }
                    }
                },
                {
                    's3': {
                        'roleArn': role_arn,
                        'bucketName': f'cms-{deployment_stage}-iot-telemetry-raw',
                        'key': 'raw-telemetry/year=${timestamp("yyyy")}/month=${timestamp("MM")}/day=${timestamp("dd")}/hour=${timestamp("HH")}/basic-ingest-${timestamp()}.json'
                    }
                },
                {
                    'cloudwatchLogs': {
                        'roleArn': role_arn,
                        'logGroupName': '/aws/iot/telemetry-test'
                    }
                }
            ],
            'ruleDisabled': False,
            'errorAction': {
                'cloudwatchLogs': {
                    'logGroupName': '/aws/iot/rule/errors',
                    'roleArn': role_arn
                }
            }
        }
        
        iot.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=rule_payload
        )
        
        print(f"✅ Created IoT rule: {rule_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error creating IoT rule: {e}")
        return False

def update_flink_app(app_name: str, bootstrap_servers: str, secret_arn: str):
    """Update Flink application with MSK IAM configuration"""
    kinesisanalytics = boto3.client('kinesisanalyticsv2')
    
    # Map application names to correct PROCESSOR_TYPE
    processor_types = {
        "event-driven-telemetry-processor": "EventDrivenTelemetryProcessor",
        "trip-processor": "TripProcessor", 
        "safety-processor": "SafetyProcessor",
        "maintenance-processor": "MaintenanceProcessor",
        "telemetry-enhanced-final": "TelemetryEnhancedProcessor"
    }
    
    # Extract processor type from app name
    processor_type = "EventDrivenTelemetryProcessor"  # default
    for key, value in processor_types.items():
        if key in app_name:
            processor_type = value
            break
    
    try:
        response = kinesisanalytics.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        
        if app_detail['ApplicationStatus'] != 'READY':
            print(f"⏳ App {app_name} not ready (status: {app_detail['ApplicationStatus']})")
            return False
        
        current_version = app_detail['ApplicationVersionId']
        
        # Update kafka configuration with MSK IAM
        env_properties = [{
            'PropertyGroupId': 'consumer.config.0',
            'PropertyMap': {
                'bootstrap.servers': bootstrap_servers,
                'security.protocol': 'SASL_SSL',
                'sasl.mechanism': 'AWS_MSK_IAM',
                'sasl.jaas.config': 'software.amazon.msk.auth.iam.IAMLoginModule required;',
                'sasl.client.callback.handler.class': 'software.amazon.msk.auth.iam.IAMClientCallbackHandler',
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': 'false',
                'aws.region': os.environ.get('AWS_REGION', 'us-east-1'),
                'PROCESSOR_TYPE': processor_type,
                'group.id': f'{app_name.split("-flink-", 1)[-1]}-consumer' if '-flink-' in app_name else f'{app_name}-consumer'
            }
        }]
        
        kinesisanalytics.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'EnvironmentPropertyUpdates': {
                    'PropertyGroups': env_properties
                }
            }
        )
        
        print(f"✅ Updated {app_name} with MSK IAM (PROCESSOR_TYPE: {processor_type})")
        return True
        
    except Exception as e:
        print(f"❌ Error updating {app_name}: {e}")
        return False

def start_flink_app(app_name: str):
    """Start Flink application"""
    kinesisanalytics = boto3.client('kinesisanalyticsv2')
    
    try:
        response = kinesisanalytics.describe_application(ApplicationName=app_name)
        status = response['ApplicationDetail']['ApplicationStatus']
        
        if status == 'RUNNING':
            print(f"✅ {app_name} already running")
            return True
        elif status == 'READY':
            kinesisanalytics.start_application(ApplicationName=app_name)
            print(f"🚀 Started {app_name}")
            return True
        else:
            print(f"⏳ {app_name} not ready to start (status: {status})")
            return False
            
    except Exception as e:
        print(f"❌ Error starting {app_name}: {e}")
        return False

def check_scram_secret_association(cluster_arn: str, secret_arn: str):
    """Check if SCRAM secret is associated with MSK cluster"""
    kafka = boto3.client('kafka')
    
    try:
        response = kafka.list_scram_secrets(ClusterArn=cluster_arn)
        associated_secrets = response.get('SecretArnList', [])
        
        if secret_arn in associated_secrets:
            print(f"✅ SCRAM secret is associated with cluster")
            return True
        else:
            print(f"❌ SCRAM secret not associated with cluster")
            print(f"   Expected: {secret_arn}")
            print(f"   Associated: {associated_secrets}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking SCRAM secret association: {e}")
        return False

def check_vpc_destination_exists():
    """Check if VPC destination exists for IoT rule"""
    iot = boto3.client('iot')
    
    try:
        destinations = iot.list_topic_rule_destinations()
        for dest in destinations.get('destinationSummaries', []):
            if dest.get('status') in ['ENABLED', 'IN_PROGRESS']:
                print(f"✅ VPC destination found: {dest['arn']} (status: {dest['status']})")
                if dest.get('status') == 'IN_PROGRESS':
                    print("⚠️  VPC destination is still provisioning, but proceeding with integration")
                return dest['arn']
        
        print("⚠️ No VPC destination found")
        return None
        
    except Exception as e:
        print(f"❌ Error checking VPC destinations: {e}")
        return None

def check_msk_vpc_connectivity(cluster_arn: str):
    """Check if MSK VPC connectivity IAM is enabled and get VPC bootstrap servers"""
    kafka = boto3.client('kafka')
    
    try:
        print(f"🔍 Checking cluster: {cluster_arn}")
        
        # Check cluster state
        response = kafka.describe_cluster_v2(ClusterArn=cluster_arn)
        cluster_info = response['ClusterInfo']
        
        if cluster_info['State'] != 'ACTIVE':
            print(f"❌ MSK cluster not ACTIVE: {cluster_info['State']}")
            return False, None
        
        print("✅ MSK cluster is ACTIVE")
        
        # Get bootstrap brokers - this is the reliable way to check VPC connectivity
        bootstrap_response = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
        vpc_bootstrap = bootstrap_response.get('BootstrapBrokerStringVpcConnectivitySaslIam')
        
        if not vpc_bootstrap:
            print("❌ VPC connectivity bootstrap servers not available")
            print("💡 VPC connectivity IAM is not enabled")
            return False, None
        
        print("✅ MSK VPC connectivity IAM is enabled")
        print(f"✅ VPC connectivity bootstrap servers: {vpc_bootstrap}")
        return True, vpc_bootstrap
        
    except Exception as e:
        print(f"❌ Error checking MSK VPC connectivity: {e}")
        return False, None

def wait_for_prerequisites(cluster_arn: str, max_wait_minutes: int = 15):
    """Wait for VPC destination and MSK VPC connectivity to be ready"""
    import time
    
    print(f"⏳ Waiting up to {max_wait_minutes} minutes for prerequisites...")
    
    for attempt in range(max_wait_minutes):
        print(f"\n🔍 Check attempt {attempt + 1}/{max_wait_minutes}:")
        
        # Check VPC destination
        vpc_dest_arn = check_vpc_destination_exists()
        
        # Check MSK VPC connectivity
        msk_ready, vpc_bootstrap = check_msk_vpc_connectivity(cluster_arn)
        
        if vpc_dest_arn and msk_ready:
            print("✅ All prerequisites ready!")
            return True, vpc_dest_arn, vpc_bootstrap
        
        if attempt < max_wait_minutes - 1:
            print("⏳ Prerequisites not ready, waiting 1 minute...")
            time.sleep(60)
    
    print(f"❌ Prerequisites not ready after {max_wait_minutes} minutes")
    return False, None, None

def build_and_upload_flink_jar(deployment_stage: str):
    """Build Flink JAR and upload to S3 (skips if JAR already exists in S3)"""
    import subprocess
    import os
    
    try:
        # Get Flink stack outputs for bucket name
        flink_outputs = get_stack_outputs(f"cms-{deployment_stage}-flink")
        bucket_name = flink_outputs.get('FlinkJarBucketOutput')
        
        if not bucket_name:
            print("❌ Flink JAR bucket not found")
            return None
        
        s3_key = "jars/cms-telemetry-processor-1.0.0.zip"
        s3 = boto3.client('s3')
        
        # Check if JAR already exists in S3 (uploaded by phase5)
        try:
            s3.head_object(Bucket=bucket_name, Key=s3_key)
            print(f"✅ JAR already exists in s3://{bucket_name}/{s3_key}, skipping build")
            return f"arn:aws:s3:::{bucket_name}", s3_key
        except:
            pass
        
        print("🔨 Building Flink JAR...")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        flink_dir = os.path.normpath(os.path.join(script_dir, "../../modules/flink"))
        result = subprocess.run(["bash", f"{flink_dir}/build.sh"], 
                              cwd=flink_dir,
                              timeout=300)
        
        if result.returncode != 0:
            print("❌ JAR build failed")
            return None
        
        print("✅ JAR built successfully")
        
        jar_path = f"{flink_dir}/target/cms-telemetry-processor-1.0.0.jar"
        print(f"📤 Uploading JAR to s3://{bucket_name}/{s3_key}")
        s3.upload_file(jar_path, bucket_name, s3_key)
        
        print("✅ JAR uploaded successfully")
        return f"arn:aws:s3:::{bucket_name}", s3_key
        
    except Exception as e:
        print(f"❌ Error building/uploading JAR: {e}")
        return None

def create_kafka_topics_via_api(deployment_stage: str):
    """Create Kafka topics using AWS API calls"""
    cf = boto3.client('cloudformation')
    kafka_client = boto3.client('kafka')
    
    try:
        # Get MSK cluster ARN
        msk_stack_name = f"cms-{deployment_stage}-msk"
        response = cf.describe_stacks(StackName=msk_stack_name)
        outputs = response['Stacks'][0]['Outputs']
        
        cluster_arn = None
        for output in outputs:
            if output['OutputKey'] == 'MSKClusterArn':
                cluster_arn = output['OutputValue']
                break
        
        if not cluster_arn:
            print("❌ Could not find MSK cluster ARN")
            return False
        
        print(f"✅ Found MSK cluster: {cluster_arn}")
        
        # Required topics with partition and replication settings
        topics = [
            {"name": "cms-telemetry-raw", "partitions": 3, "replication": 2},
            {"name": "cms-telemetry-processed", "partitions": 3, "replication": 2},
            {"name": "cms-telemetry-maintenance", "partitions": 3, "replication": 2},
            {"name": "cms-trip-events", "partitions": 3, "replication": 2},
            {"name": "cms-safety-events", "partitions": 3, "replication": 2},
            {"name": "cms-maintenance-events", "partitions": 3, "replication": 2}
        ]
        
        print("🔄 Creating Kafka topics via AWS API...")
        success_count = 0
        
        for topic in topics:
            try:
                kafka_client.create_topic(
                    ClusterArn=cluster_arn,
                    TopicName=topic["name"],
                    PartitionCount=topic["partitions"],
                    ReplicationFactor=topic["replication"]
                )
                print(f"  ✅ Created topic: {topic['name']}")
                success_count += 1
                
            except kafka_client.exceptions.ConflictException:
                print(f"  ✅ Topic already exists: {topic['name']}")
                success_count += 1
            except Exception as e:
                print(f"  ❌ Failed to create {topic['name']}: {e}")
        
        print(f"✅ Successfully created/verified {success_count}/{len(topics)} topics")
        return success_count == len(topics)
        
    except Exception as e:
        print(f"❌ Error creating topics: {e}")
        return False

def add_vpc_configuration_to_flink_applications(deployment_stage: str):
    """Add VPC configuration to Flink applications for MSK connectivity"""
    flink = boto3.client('kinesisanalyticsv2')
    cf = boto3.client('cloudformation')
    
    # Get MSK VPC configuration from CloudFormation
    try:
        msk_stack_name = f"cms-{deployment_stage}-msk"
        response = cf.describe_stacks(StackName=msk_stack_name)
        outputs = response['Stacks'][0]['Outputs']
        
        msk_vpc_id = None
        msk_security_group_id = None
        msk_subnet_ids = None
        
        for output in outputs:
            if output['OutputKey'] == 'MSKVpcId':
                msk_vpc_id = output['OutputValue']
            elif output['OutputKey'] == 'MSKSecurityGroupId':
                msk_security_group_id = output['OutputValue']
            elif output['OutputKey'] == 'MSKPrivateSubnetIds':
                msk_subnet_ids = output['OutputValue'].split(',')
        
        if not all([msk_vpc_id, msk_security_group_id, msk_subnet_ids]):
            print("❌ Could not find MSK VPC configuration in CloudFormation outputs")
            return False
            
        print(f"✅ Found MSK VPC configuration:")
        print(f"  VPC: {msk_vpc_id}")
        print(f"  Security Group: {msk_security_group_id}")
        print(f"  Subnets: {', '.join(msk_subnet_ids[:2])}")
        
    except Exception as e:
        print(f"❌ Error getting MSK VPC configuration: {e}")
        return False
    
    applications = [
        f"cms-{deployment_stage}-flink-event-driven-telemetry-processor",
        f"cms-{deployment_stage}-flink-trip-processor", 
        f"cms-{deployment_stage}-flink-safety-processor",
        f"cms-{deployment_stage}-flink-maintenance-processor",
        f"cms-{deployment_stage}-flink-telemetry-enhanced-final"
    ]
    
    print("🔄 Adding VPC configuration to Flink applications...")
    success_count = 0
    
    for app_name in applications:
        try:
            # Get current application configuration
            response = flink.describe_application(ApplicationName=app_name)
            app_detail = response['ApplicationDetail']
            
            # Check if VPC configuration already exists
            if 'VpcConfigurationDescriptions' in app_detail['ApplicationConfigurationDescription']:
                print(f"  ✅ {app_name} already has VPC configuration")
                success_count += 1
                continue
            
            if app_detail['ApplicationStatus'] != 'READY':
                print(f"  ⚠️  {app_name} is not in READY state ({app_detail['ApplicationStatus']}), skipping")
                continue
            
            # Add VPC configuration
            vpc_config = {
                'SubnetIds': msk_subnet_ids[:2],  # Use first 2 subnets
                'SecurityGroupIds': [msk_security_group_id]
            }
            
            print(f"  🔄 Adding VPC configuration to {app_name}...")
            
            flink.add_application_vpc_configuration(
                ApplicationName=app_name,
                CurrentApplicationVersionId=app_detail['ApplicationVersionId'],
                VpcConfiguration=vpc_config
            )
            
            print(f"  ✅ Added VPC configuration to {app_name}")
            success_count += 1
            
        except Exception as e:
            print(f"  ❌ Error updating {app_name}: {e}")
            continue
    
    print(f"✅ Successfully added VPC configuration to {success_count}/{len(applications)} applications")
    return success_count == len(applications)

def update_flink_applications_with_jar(deployment_stage: str, bucket_arn: str, jar_key: str):
    """Update all Flink applications with JAR code"""
    flink = boto3.client('kinesisanalyticsv2')
    
    applications = [
        f"cms-{deployment_stage}-flink-event-driven-telemetry-processor",
        f"cms-{deployment_stage}-flink-trip-processor", 
        f"cms-{deployment_stage}-flink-safety-processor",
        f"cms-{deployment_stage}-flink-maintenance-processor",
        f"cms-{deployment_stage}-flink-telemetry-enhanced-final"
    ]
    
    print("🔄 Updating Flink applications with JAR code...")
    success_count = 0
    
    for app_name in applications:
        try:
            response = flink.describe_application(ApplicationName=app_name)
            app_detail = response['ApplicationDetail']
            
            if app_detail['ApplicationStatus'] != 'READY':
                print(f"⚠️ {app_name} not READY: {app_detail['ApplicationStatus']}")
                continue
            
            current_version = app_detail['ApplicationVersionId']
            
            flink.update_application(
                ApplicationName=app_name,
                CurrentApplicationVersionId=current_version,
                ApplicationConfigurationUpdate={
                    'ApplicationCodeConfigurationUpdate': {
                        'CodeContentUpdate': {
                            'S3ContentLocationUpdate': {
                                'BucketARNUpdate': bucket_arn,
                                'FileKeyUpdate': jar_key
                            }
                        },
                        'CodeContentTypeUpdate': 'ZIPFILE'
                    }
                }
            )
            
            print(f"✅ Updated {app_name}")
            success_count += 1
            
        except Exception as e:
            print(f"❌ Error updating {app_name}: {e}")
    
    print(f"🎉 Updated {success_count}/{len(applications)} applications")
    return success_count == len(applications)

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    
    print(f"🚀 Complete Integration for stage: {deployment_stage}")
    
    # Get stack outputs
    msk_outputs = get_stack_outputs(f"cms-{deployment_stage}-msk")
    iot_outputs = get_stack_outputs(f"cms-{deployment_stage}-iot")
    flink_outputs = get_stack_outputs(f"cms-{deployment_stage}-flink")
    
    if not all([msk_outputs.get('MSKClusterArn'), iot_outputs, flink_outputs]):
        print("❌ Required stacks not found")
        return False
    
    cluster_arn = msk_outputs['MSKClusterArn']
    # Get secret ARN from MSK stack outputs (now uses proper AmazonMSK_ prefix)
    secret_arn = msk_outputs.get('IoTUserSecretArn', '')
    role_arn = iot_outputs.get('IoTRoleArn', '')
    
    # Step 0: Build and upload Flink JAR
    print("\n🔨 Building and uploading Flink JAR...")
    jar_info = build_and_upload_flink_jar(deployment_stage)
    if not jar_info:
        print("❌ Failed to build/upload JAR")
        return False
    
    bucket_arn, jar_key = jar_info
    
    # Step 1: Add VPC configuration to Flink applications
    print("\n🔗 Adding VPC configuration to Flink applications...")
    if not add_vpc_configuration_to_flink_applications(deployment_stage):
        print("❌ Failed to add VPC configuration to Flink applications")
        return False
    
    # Step 2: Check prerequisites in correct order - VPC connectivity first, then SCRAM
    print("\n🔍 Checking prerequisites...")
    
    # Check VPC connectivity first (cluster must be ready)
    print("🔍 Checking MSK VPC connectivity...")
    vpc_ready, vpc_bootstrap = check_msk_vpc_connectivity(cluster_arn)
    
    if not vpc_ready:
        print("⏳ VPC connectivity not ready yet - waiting for MSK cluster update to complete...")
        print("   This typically takes 10-15 minutes if VPC connectivity is still being enabled")
        
        # Wait for VPC connectivity to be enabled
        for attempt in range(30):  # Wait up to 30 minutes
            print(f"   Attempt {attempt + 1}/30: Checking VPC connectivity status...")
            
            vpc_ready, vpc_bootstrap = check_msk_vpc_connectivity(cluster_arn)
            if vpc_ready:
                print("✅ VPC connectivity is now enabled!")
                break
            
            if attempt < 29:  # Don't sleep on the last attempt
                print("   VPC connectivity not ready yet, waiting 1 minute...")
                import time
                time.sleep(60)
        
        if not vpc_ready:
            print("❌ VPC connectivity not enabled after 30 minutes")
            print("💡 Check AWS MSK Console to see if cluster is still updating")
            print("💡 Or run VPC connectivity script manually:")
            print(f"   python3 scripts/enable_vpc_connectivity.py {deployment_stage} $AWS_PROFILE")
            return False

    # Check SCRAM secret association (depends on VPC connectivity being ready)
    print("🔍 Checking SCRAM secret association...")
    scram_ready = check_scram_secret_association(cluster_arn, secret_arn)
    
    if not scram_ready:
        print("⏳ SCRAM secret not associated yet - waiting for association to complete...")
        print("   SCRAM association happens after VPC connectivity is enabled")
        
        # Wait for SCRAM secret to be associated
        for attempt in range(15):  # Wait up to 15 minutes (shorter since VPC is ready)
            print(f"   Attempt {attempt + 1}/15: Checking SCRAM secret association...")
            
            scram_ready = check_scram_secret_association(cluster_arn, secret_arn)
            if scram_ready:
                print("✅ SCRAM secret is now associated!")
                break
            
            if attempt < 14:  # Don't sleep on the last attempt
                print("   SCRAM secret not associated yet, waiting 1 minute...")
                import time
                time.sleep(60)
        
        if not scram_ready:
            print("❌ SCRAM secret not associated after 15 minutes")
            print("💡 VPC connectivity is ready but SCRAM association failed")
            print("💡 Run VPC connectivity script to complete SCRAM setup:")
            print(f"   python3 scripts/enable_vpc_connectivity.py {deployment_stage} $AWS_PROFILE")
            return False
    
    # Step 1: Update Flink applications with JAR
    print("\n📦 Updating Flink applications with JAR...")
    if not update_flink_applications_with_jar(deployment_stage, bucket_arn, jar_key):
        print("⚠️  Some Flink applications failed to update, but continuing with integration...")
        # Don't return False - continue with IoT integration
    
    # Step 2: Get MSK bootstrap servers
    print("\n🔍 Getting MSK bootstrap servers...")
    
    # Get SCRAM bootstrap servers for IoT rule (port 9096)
    scram_bootstrap_servers = get_msk_bootstrap_servers_scram(cluster_arn)
    
    if not scram_bootstrap_servers:
        print("❌ Could not get SCRAM bootstrap servers")
        return False
    
    print(f"✅ SCRAM bootstrap servers: {scram_bootstrap_servers}")
    
    # Get regular IAM bootstrap servers for Flink applications (port 9098)
    regular_bootstrap_servers = get_msk_bootstrap_servers(cluster_arn)
    
    if not regular_bootstrap_servers:
        print("❌ Could not get regular bootstrap servers")
        return False
    
    print(f"✅ Regular bootstrap servers: {regular_bootstrap_servers}")
    
    # Step 3: Create IoT rule first (this creates the VPC destination)
    print("\n📡 Creating IoT rule and VPC destination...")
    iot_success = create_iot_rule_with_scram(
        rule_name="cms_telemetry_to_msk",
        cluster_arn=cluster_arn,
        bootstrap_servers=scram_bootstrap_servers,  # Use SCRAM servers
        secret_arn=secret_arn,
        role_arn=role_arn,
        deployment_stage=deployment_stage
    )
    
    if not iot_success:
        print("❌ Failed to create IoT rule")
        return False
    
    # Step 4: Check VPC destination exists
    print("\n🔍 Checking VPC destination...")
    vpc_dest_arn = check_vpc_destination_exists()
    if not vpc_dest_arn:
        print("⚠️  VPC destination not found - IoT to MSK routing may not work")
    else:
        print(f"✅ VPC destination: {vpc_dest_arn}")

    # Step 5: Get IAM bootstrap servers for Flink (regular, not VPC connectivity)
    iam_bootstrap = get_msk_bootstrap_servers(cluster_arn)
    if not iam_bootstrap:
        print("❌ Could not get IAM bootstrap servers")
        return False
    print(f"✅ IAM bootstrap servers (for Flink): {iam_bootstrap}")
    print(f"✅ SCRAM bootstrap servers (for IoT): {regular_bootstrap_servers}")
    
    # Step 6: Update Flink applications with IAM bootstrap servers (they're in the VPC)
    print("\n⚡ Updating Flink applications with regular bootstrap servers (VPC deployment)...")
    apps_to_update = [
        f'cms-{deployment_stage}-flink-event-driven-telemetry-processor',
        f'cms-{deployment_stage}-flink-telemetry-enhanced-final',
        f'cms-{deployment_stage}-flink-trip-processor',
        f'cms-{deployment_stage}-flink-safety-processor',
        f'cms-{deployment_stage}-flink-maintenance-processor'
    ]
    
    flink_success = 0
    for app_name in apps_to_update:
        if update_flink_app(app_name, regular_bootstrap_servers, secret_arn):
            flink_success += 1
    
    # Step 6: Start Flink applications
    print("\n🚀 Starting Flink applications...")
    started_count = 0
    for app_name in apps_to_update:
        if start_flink_app(app_name):
            started_count += 1
            print(f"🚀 Started {app_name}")
        import time
        time.sleep(2)  # Brief delay between starts
    
    print(f"\n🎉 Integration Summary:")
    print(f"  IoT Rule: {'✅' if iot_success else '❌'}")
    print(f"  Flink Apps Updated: {flink_success}/{len(apps_to_update)}")
    print(f"  Flink Apps Started: {started_count}/{len(apps_to_update)}")
    
    return iot_success and flink_success > 0

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
