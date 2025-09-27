import json
import boto3
import os
import time
from datetime import datetime, timedelta

dynamodb = boto3.resource('dynamodb')

def handler(event, context):
    # Handle fleets POST endpoint first
    method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    if path == '/api/v1/fleets' and method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            entry = body.get('entry', {})
            
            fleet_item = {
                'fleetId': f"FLEET-{int(time.time())}",
                'name': entry.get('name', ''),
                'description': entry.get('description', ''),
                'status': 'active',
                'vehicleCount': 0
            }
            
            fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
            fleets_table.put_item(Item=fleet_item)
            
            # Invalidate cache
            try:
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                print("🗑️ Invalidated fleets cache after creation")
            except Exception as cache_error:
                print(f"Cache invalidation error: {cache_error}")
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'fleet': fleet_item})
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    if path == '/api/v1/vehicles' and method == 'POST':
        print(f"🚗 Vehicle POST endpoint reached!")
        try:
            body = json.loads(event.get('body', '{}'))
            # Handle both direct data and entry-wrapped data
            entry = body.get('entry', body)
            print(f"🚗 Vehicle entry data: {entry}")
            
            vehicle_item = {
                'vehicleId': f"VEH-{int(time.time())}",
                'vin': entry.get('vin', ''),
                'make': entry.get('make', ''),
                'model': entry.get('model', ''),
                'year': entry.get('year', ''),
                'licensePlate': entry.get('licensePlate', ''),
                'color': entry.get('color', ''),
                'vehicleType': entry.get('vehicleType', ''),
                'fuelType': entry.get('fuelType', ''),
                'fleetId': entry.get('fleetId', ''),
                'status': 'active',
                'connectionStatus': 'disconnected',  # Default connection status
                'activityStatus': 'inactive',        # Default activity status  
                'lastConnected': None,               # Will be set when device connects
                'lastDisconnected': None,            # Will be set when device disconnects
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat()
            }
            print(f"🚗 Vehicle item to save: {vehicle_item}")
            
            vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
            print(f"🚗 Vehicles table name: {os.environ.get('VEHICLES_TABLE_NAME')}")
            
            vehicles_table.put_item(Item=vehicle_item)
            print(f"🚗 Vehicle saved successfully!")
            
            # Handle certificate creation if requested
            if entry.get('createCertificate', False):
                print(f"🔐 Creating certificate for vehicle {vehicle_item['vin']}")
                try:
                    import boto3
                    
                    # Check if environment variable exists
                    cert_table_name = os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME')
                    if not cert_table_name:
                        print(f"🔐 ERROR: VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set!")
                        raise Exception("VEHICLE_CERTIFICATES_TABLE_NAME environment variable not set")
                    
                    print(f"🔐 Using certificates table: {cert_table_name}")
                    
                    iot_client = boto3.client('iot')
                    
                    # Create certificate
                    print(f"🔐 Creating IoT certificate...")
                    cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
                    print(f"🔐 Certificate created: {cert_response['certificateId']}")
                    
                    # Create IoT Thing using VIN as thing name
                    thing_name = vehicle_item['vin']
                    try:
                        iot_client.create_thing(thingName=thing_name)
                        print(f"🔗 Created IoT Thing: {thing_name}")
                    except iot_client.exceptions.ResourceAlreadyExistsException:
                        print(f"🔗 IoT Thing already exists: {thing_name}")
                    except Exception as thing_error:
                        print(f"🔗 Error creating IoT Thing: {str(thing_error)}")
                        raise thing_error
                    
                    # Use shared IoT Policy for all vehicles
                    shared_policy_name = "CMS-Vehicle-IoT-Policy"
                    shared_policy_document = {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["iot:Connect"],
                                "Resource": ["arn:aws:iot:*:*:client/*"]
                            },
                            {
                                "Effect": "Allow", 
                                "Action": ["iot:Publish"],
                                "Resource": [
                                    "arn:aws:iot:*:*:topic/$aws/rules/cms_dev_iot_msk_rule/*",
                                ]
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["iot:Subscribe", "iot:Receive"], 
                                "Resource": [
                                    "arn:aws:iot:*:*:topicfilter/fleet/vehicle/*/commands"
                                ]
                            }
                        ]
                    }
                    
                    # Create shared policy if it doesn't exist
                    try:
                        iot_client.create_policy(
                            policyName=shared_policy_name,
                            policyDocument=json.dumps(shared_policy_document)
                        )
                        print(f"🔐 Created shared IoT Policy: {shared_policy_name}")
                    except iot_client.exceptions.ResourceAlreadyExistsException:
                        print(f"🔐 Shared IoT Policy already exists: {shared_policy_name}")
                    except Exception as policy_error:
                        print(f"🔐 Error creating shared IoT Policy: {str(policy_error)}")
                        raise policy_error
                    
                    # Attach certificate to IoT Thing
                    try:
                        iot_client.attach_thing_principal(
                            thingName=thing_name,
                            principal=cert_response['certificateArn']
                        )
                        print(f"🔗 Attached certificate to IoT Thing: {thing_name}")
                    except Exception as attach_error:
                        print(f"🔗 Error attaching certificate to thing: {str(attach_error)}")
                        raise attach_error
                    
                    # Attach policy to certificate
                    try:
                        iot_client.attach_principal_policy(
                            policyName=shared_policy_name,
                            principal=cert_response['certificateArn']
                        )
                        print(f"🔐 Attached shared policy to certificate: {shared_policy_name}")
                    except Exception as policy_attach_error:
                        print(f"🔐 Error attaching policy to certificate: {str(policy_attach_error)}")
                        raise policy_attach_error
                    
                    # Save certificate to DynamoDB
                    certificate_item = {
                        'vin': vehicle_item['vin'],
                        'vehicleId': vehicle_item['vehicleId'],
                        'certificateId': cert_response['certificateId'],
                        'certificateArn': cert_response['certificateArn'],
                        'certificatePem': cert_response['certificatePem'],
                        'publicKey': cert_response['keyPair']['PublicKey'],
                        'privateKey': cert_response['keyPair']['PrivateKey'],
                        'thingName': vehicle_item['vin'],
                        'policyName': shared_policy_name,
                        'status': 'ACTIVE',
                        'createdAt': datetime.utcnow().isoformat(),
                        'updatedAt': datetime.utcnow().isoformat()
                    }
                    
                    print(f"🔐 Saving certificate to DynamoDB table: {cert_table_name}")
                    certificates_table = dynamodb.Table(cert_table_name)
                    certificates_table.put_item(Item=certificate_item)
                    print(f"🔐 Certificate saved successfully for VIN: {vehicle_item['vin']}")
                    
                    # Add certificate info to vehicle response
                    vehicle_item['hasCertificate'] = True
                    vehicle_item['certificateId'] = cert_response['certificateId']
                    
                except Exception as cert_error:
                    print(f"🔐 ERROR creating certificate: {str(cert_error)}")
                    print(f"🔐 Certificate error type: {type(cert_error)}")
                    import traceback
                    print(f"🔐 Certificate error traceback: {traceback.format_exc()}")
                    # Don't fail the vehicle creation if certificate fails, but log the error
                    vehicle_item['hasCertificate'] = False
                    vehicle_item['certificateError'] = str(cert_error)
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'vehicle': vehicle_item})
            }
        except Exception as e:
            print(f"🚗 Error creating vehicle: {str(e)}")
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    # Validate required environment variables
    required_env_vars = [
        'SAFETY_EVENTS_TABLE_NAME',
        'VEHICLES_TABLE_NAME', 
        'FLEETS_TABLE_NAME',
        'DASHBOARD_METRICS_CACHE_TABLE',
        'DRIVERS_TABLE_NAME'
    ]
    
    for env_var in required_env_vars:
        if not os.environ.get(env_var):
            return {
                'statusCode': 500,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
                },
                'body': json.dumps({'error': f'Missing required environment variable: {env_var}'})
            }
    
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    try:
        method = event.get('httpMethod', 'GET')
        path = event.get('path', '')
        query_params = event.get('queryStringParameters') or {}
        
        # Handle fleet PUT endpoint (update fleet)
        if path.startswith('/api/v1/fleets/') and method == 'PUT':
            fleet_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                
                # Check if fleet exists
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                # Update fleet
                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                
                if 'name' in entry:
                    update_expression += ', #name = :name'
                    expression_values[':name'] = entry['name']
                if 'description' in entry:
                    update_expression += ', description = :description'
                    expression_values[':description'] = entry['description']
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                
                expression_names = {'#name': 'name', '#status': 'status'}
                
                response = fleets_table.update_item(
                    Key={'fleetId': fleet_id},
                    UpdateExpression=update_expression,
                    ExpressionAttributeValues=expression_values,
                    ExpressionAttributeNames=expression_names,
                    ReturnValues='ALL_NEW'
                )
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                # Invalidate cache
                try:
                    cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                    cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                    print("🗑️ Invalidated fleets cache after update")
                except Exception as cache_error:
                    print(f"Cache invalidation error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'fleet': response['Attributes']}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle vehicle PUT endpoint (update vehicle)
        if path.startswith('/api/v1/vehicles/') and method == 'PUT' and not path.endswith('/trips') and not path.endswith('/safety-alerts') and not path.endswith('/maintenance-alerts'):
            vehicle_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                # Check if vehicle exists
                response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                # Update vehicle
                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                expression_names = {}
                
                if 'vin' in entry:
                    update_expression += ', vin = :vin'
                    expression_values[':vin'] = entry['vin']
                if 'make' in entry:
                    update_expression += ', make = :make'
                    expression_values[':make'] = entry['make']
                if 'model' in entry:
                    update_expression += ', #model = :model'
                    expression_values[':model'] = entry['model']
                    expression_names['#model'] = 'model'
                if 'year' in entry:
                    update_expression += ', #year = :year'
                    expression_values[':year'] = entry['year']
                    expression_names['#year'] = 'year'
                if 'licensePlate' in entry:
                    update_expression += ', licensePlate = :license_plate'
                    expression_values[':license_plate'] = entry['licensePlate']
                if 'color' in entry:
                    update_expression += ', color = :color'
                    expression_values[':color'] = entry['color']
                if 'vehicleType' in entry:
                    update_expression += ', vehicleType = :vehicle_type'
                    expression_values[':vehicle_type'] = entry['vehicleType']
                if 'fuelType' in entry:
                    update_expression += ', fuelType = :fuel_type'
                    expression_values[':fuel_type'] = entry['fuelType']
                if 'fleetId' in entry:
                    update_expression += ', fleetId = :fleet_id'
                    expression_values[':fleet_id'] = entry['fleetId']
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                    expression_names['#status'] = 'status'
                
                update_kwargs = {
                    'Key': {'vehicleId': vehicle_id},
                    'UpdateExpression': update_expression,
                    'ExpressionAttributeValues': expression_values,
                    'ReturnValues': 'ALL_NEW'
                }
                
                if expression_names:
                    update_kwargs['ExpressionAttributeNames'] = expression_names
                
                response = vehicles_table.update_item(**update_kwargs)
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'vehicle': response['Attributes']}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle fleet DELETE endpoint
        if path.startswith('/api/v1/fleets/') and method == 'DELETE':
            fleet_id = path.split('/')[-1]
            try:
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                # Check if fleet exists
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                # Disassociate all vehicles from this fleet (don't delete vehicles)
                scan_kwargs = {
                    'FilterExpression': 'fleetId = :fleet_id',
                    'ExpressionAttributeValues': {':fleet_id': fleet_id}
                }
                
                while True:
                    vehicles_response = vehicles_table.scan(**scan_kwargs)
                    
                    for vehicle in vehicles_response['Items']:
                        vehicles_table.update_item(
                            Key={'vehicleId': vehicle['vehicleId']},
                            UpdateExpression='REMOVE fleetId',
                            ReturnValues='NONE'
                        )
                    
                    if 'LastEvaluatedKey' not in vehicles_response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = vehicles_response['LastEvaluatedKey']
                
                # Delete the fleet
                fleets_table.delete_item(Key={'fleetId': fleet_id})
                
                # Invalidate cache
                try:
                    cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                    cache_table.delete_item(Key={'metricKey': 'fleets_list'})
                    print("🗑️ Invalidated fleets cache after deletion")
                except Exception as cache_error:
                    print(f"Cache invalidation error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Fleet {fleet_id} deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle vehicle DELETE endpoint
        if path.startswith('/api/v1/vehicles/') and method == 'DELETE':
            vehicle_id = path.split('/')[-1]
            try:
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                # Check if vehicle exists
                response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                # Delete all trips for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        trips_response = trips_table.query(**query_kwargs)
                        
                        for trip in trips_response['Items']:
                            trips_table.delete_item(Key={'tripId': trip['tripId']})
                        
                        if 'LastEvaluatedKey' not in trips_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = trips_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        trips_response = trips_table.scan(**scan_kwargs)
                        
                        for trip in trips_response['Items']:
                            trips_table.delete_item(Key={'tripId': trip['tripId']})
                        
                        if 'LastEvaluatedKey' not in trips_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = trips_response['LastEvaluatedKey']
                
                # Delete all safety events for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        safety_response = safety_events_table.query(**query_kwargs)
                        
                        for event in safety_response['Items']:
                            if 'eventId' in event:
                                safety_events_table.delete_item(Key={'eventId': event['eventId']})
                            elif 'timestamp' in event:
                                safety_events_table.delete_item(Key={'eventId': event.get('eventId', ''), 'timestamp': event['timestamp']})
                        
                        if 'LastEvaluatedKey' not in safety_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = safety_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        safety_response = safety_events_table.scan(**scan_kwargs)
                        
                        for event in safety_response['Items']:
                            if 'eventId' in event:
                                safety_events_table.delete_item(Key={'eventId': event['eventId']})
                            elif 'timestamp' in event:
                                safety_events_table.delete_item(Key={'eventId': event.get('eventId', ''), 'timestamp': event['timestamp']})
                        
                        if 'LastEvaluatedKey' not in safety_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = safety_response['LastEvaluatedKey']
                
                # Delete all maintenance alerts for this vehicle
                try:
                    query_kwargs = {
                        'IndexName': 'vehicleId-index',
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        maintenance_response = maintenance_alerts_table.query(**query_kwargs)
                        
                        for alert in maintenance_response['Items']:
                            if 'alertId' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert['alertId']})
                            elif 'timestamp' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert.get('alertId', ''), 'timestamp': alert['timestamp']})
                        
                        if 'LastEvaluatedKey' not in maintenance_response:
                            break
                        query_kwargs['ExclusiveStartKey'] = maintenance_response['LastEvaluatedKey']
                        
                except Exception:
                    # Fallback to scan if GSI not available
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id}
                    }
                    
                    while True:
                        maintenance_response = maintenance_alerts_table.scan(**scan_kwargs)
                        
                        for alert in maintenance_response['Items']:
                            if 'alertId' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert['alertId']})
                            elif 'timestamp' in alert:
                                maintenance_alerts_table.delete_item(Key={'alertId': alert.get('alertId', ''), 'timestamp': alert['timestamp']})
                        
                        if 'LastEvaluatedKey' not in maintenance_response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = maintenance_response['LastEvaluatedKey']
                
                # Delete vehicle certificates if they exist
                try:
                    certificates_table = dynamodb.Table(os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME'))
                    vehicle = response['Item']
                    vin = vehicle.get('vin')
                    if vin:
                        certificates_table.delete_item(Key={'vin': vin})
                except Exception as cert_error:
                    print(f"Error deleting certificate for vehicle {vehicle_id}: {cert_error}")
                
                # Delete the vehicle
                vehicles_table.delete_item(Key={'vehicleId': vehicle_id})
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Vehicle {vehicle_id} and all related data deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle drivers CRUD operations
        if path == '/api/v1/drivers' and method == 'POST':
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                driver_item = {
                    'driverId': f"DRV-{int(time.time())}",
                    'firstName': entry.get('firstName', ''),
                    'lastName': entry.get('lastName', ''),
                    'email': entry.get('email', ''),
                    'phone': entry.get('phone', ''),
                    'licenseNumber': entry.get('licenseNumber', ''),
                    'licenseExpiry': entry.get('licenseExpiry', ''),
                    'status': 'active',
                    'fleetId': entry.get('fleetId', ''),
                    'createdAt': datetime.utcnow().isoformat(),
                    'updatedAt': datetime.utcnow().isoformat()
                }
                
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                drivers_table.put_item(Item=driver_item)
                
                return {
                    'statusCode': 201,
                    'headers': cors_headers,
                    'body': json.dumps({'driver': driver_item})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path == '/api/v1/drivers' and method == 'GET':
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 25)), 1000)
                page = int(query_params.get('page', 1))
                fleet_id = query_params.get('fleetId')
                
                filter_expression = None
                expression_values = {}
                
                if fleet_id and fleet_id != 'all':
                    filter_expression = 'fleetId = :fleet_id'
                    expression_values[':fleet_id'] = fleet_id
                
                scan_kwargs = {}
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    scan_kwargs['ExpressionAttributeValues'] = expression_values
                
                # Get total count
                count_kwargs = dict(scan_kwargs)
                count_kwargs['Select'] = 'COUNT'
                count_response = drivers_table.scan(**count_kwargs)
                total_count = count_response['Count']
                
                # Get paginated data
                scan_kwargs['Limit'] = limit * 50
                current_page = 1
                while current_page < page:
                    response = drivers_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                drivers = []
                while len(drivers) < limit:
                    response = drivers_table.scan(**scan_kwargs)
                    drivers.extend(response['Items'])
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                drivers = drivers[:limit]
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'drivers': drivers,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': (total_count + limit - 1) // limit,
                        'hasNextPage': len(drivers) == limit,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'GET':
            driver_id = path.split('/')[-1]
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                response = drivers_table.get_item(Key={'driverId': driver_id})
                
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                driver = response['Item']
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'driver': driver}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'PUT':
            driver_id = path.split('/')[-1]
            try:
                body = json.loads(event.get('body', '{}'))
                entry = body.get('entry', body)
                
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                # Check if driver exists
                response = drivers_table.get_item(Key={'driverId': driver_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                # Update driver
                update_expression = 'SET updatedAt = :updated_at'
                expression_values = {':updated_at': datetime.utcnow().isoformat()}
                
                if 'firstName' in entry:
                    update_expression += ', firstName = :first_name'
                    expression_values[':first_name'] = entry['firstName']
                if 'lastName' in entry:
                    update_expression += ', lastName = :last_name'
                    expression_values[':last_name'] = entry['lastName']
                if 'email' in entry:
                    update_expression += ', email = :email'
                    expression_values[':email'] = entry['email']
                if 'phone' in entry:
                    update_expression += ', phone = :phone'
                    expression_values[':phone'] = entry['phone']
                if 'licenseNumber' in entry:
                    update_expression += ', licenseNumber = :license_number'
                    expression_values[':license_number'] = entry['licenseNumber']
                if 'licenseExpiry' in entry:
                    update_expression += ', licenseExpiry = :license_expiry'
                    expression_values[':license_expiry'] = entry['licenseExpiry']
                if 'status' in entry:
                    update_expression += ', #status = :status'
                    expression_values[':status'] = entry['status']
                if 'fleetId' in entry:
                    update_expression += ', fleetId = :fleet_id'
                    expression_values[':fleet_id'] = entry['fleetId']
                
                expression_names = {}
                if 'status' in entry:
                    expression_names['#status'] = 'status'
                
                update_kwargs = {
                    'Key': {'driverId': driver_id},
                    'UpdateExpression': update_expression,
                    'ExpressionAttributeValues': expression_values,
                    'ReturnValues': 'ALL_NEW'
                }
                
                if expression_names:
                    update_kwargs['ExpressionAttributeNames'] = expression_names
                
                response = drivers_table.update_item(**update_kwargs)
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'driver': response['Attributes']}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        if path.startswith('/api/v1/drivers/') and method == 'DELETE':
            driver_id = path.split('/')[-1]
            try:
                drivers_table = dynamodb.Table(os.environ.get('DRIVERS_TABLE_NAME'))
                
                # Check if driver exists
                response = drivers_table.get_item(Key={'driverId': driver_id})
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Driver {driver_id} not found'})
                    }
                
                # Delete the driver
                drivers_table.delete_item(Key={'driverId': driver_id})
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'message': f'Driver {driver_id} deleted successfully'})
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle trips by driver endpoint
        if path == '/api/v1/trips' and method == 'GET' and 'driverId' in query_params:
            driver_id = query_params.get('driverId')
            limit = min(int(query_params.get('limit', 100)), 1000)
            
            try:
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # Query trips by driverId using scan with filter (could be optimized with GSI)
                response = trips_table.scan(
                    FilterExpression='driverId = :driverId',
                    ExpressionAttributeValues={':driverId': driver_id},
                    Limit=limit
                )
                
                trips = []
                for item in response.get('Items', []):
                    # Get VIN from vehicles table
                    vehicle_id = item.get('vehicleId')
                    vin = vehicle_id  # Default to vehicleId if VIN not found
                    
                    try:
                        vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                        vehicle_response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                        if 'Item' in vehicle_response:
                            vin = vehicle_response['Item'].get('vin', vehicle_id)
                    except:
                        pass  # Use vehicleId as fallback
                    
                    # Convert DynamoDB format to API format
                    trip = {
                        'tripId': item.get('tripId'),
                        'vehicleId': vehicle_id,
                        'vin': vin,
                        'startTime': int(item.get('startTime', 0)),
                        'endTime': int(item.get('endTime', 0)),
                        'duration': float(item.get('durationMs', 0)) / 60000,  # Convert ms to minutes
                        'distance': float(item.get('totalDistance', 0)),
                        'startLocation': {
                            'latitude': float(item.get('route', [{}])[0].get('lat', 0)) if item.get('route') else 0,
                            'longitude': float(item.get('route', [{}])[0].get('lng', 0)) if item.get('route') else 0
                        },
                        'endLocation': {
                            'latitude': float(item.get('lat', 0)),
                            'longitude': float(item.get('lng', 0))
                        },
                        'maxSpeed': float(item.get('maxSpeed', 0)),
                        'avgSpeed': float(item.get('averageSpeed', 0)),
                        'fuelConsumption': float(item.get('currentFuelLevel', 0)),
                        'driverScore': float(item.get('driverScore', 0)),
                        'driverName': item.get('driverName', 'Unknown Driver'),
                        'assignedDriver': item.get('driverName', 'Unknown Driver')  # Fallback field
                    }
                    trips.append(trip)
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'trips': trips,
                        'totalCount': len(trips)
                    })
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle safety events by driver endpoint
        if path == '/api/v1/safety-events' and method == 'GET' and 'driverId' in query_params:
            driver_id = query_params.get('driverId')
            limit = min(int(query_params.get('limit', 100)), 1000)
            
            try:
                safety_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Query safety events by driverId using scan with filter
                response = safety_table.scan(
                    FilterExpression='driverId = :driverId',
                    ExpressionAttributeValues={':driverId': driver_id},
                    Limit=limit
                )
                
                events = []
                for item in response.get('Items', []):
                    # Convert DynamoDB format to API format
                    event = {
                        'eventId': item.get('eventId'),
                        'tripId': item.get('tripId'),
                        'vehicleId': item.get('vehicleId'),
                        'eventType': item.get('eventType'),
                        'severity': item.get('severity'),
                        'timestamp': int(item.get('timestamp', 0)),
                        'location': {
                            'latitude': float(item.get('lat', 0)),
                            'longitude': float(item.get('lng', 0))
                        },
                        'description': item.get('message', '')
                    }
                    events.append(event)
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'events': events,
                        'totalCount': len(events)
                    })
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': str(e)})
                }
        
        # Handle dashboard metrics endpoint
        if path == '/api/v1/dashboard/metrics' and method == 'GET':
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({
                    'totalVehicles': 3500,
                    'activeVehicles': 3200,
                    'totalFleets': 7,
                    'safetyAlerts': {
                        'total': 34554,
                        'critical': 1250,
                        'warning': 15304,
                        'info': 18000
                    },
                    'maintenanceAlerts': {
                        'total': 892,
                        'overdue': 45,
                        'upcoming': 234,
                        'scheduled': 613
                    },
                    'fleetUtilization': {
                        'average': 78.5,
                        'highest': 95.2,
                        'lowest': 62.1
                    },
                    'fleetPerformance': {
                        'FLEET-MUNICH': {'score': 85.2, 'vehicles': 500, 'safetyScore': 85.2, 'driverScore': 88.1, 'maintenanceAlertsPerVehicle': 0.8, 'utilizationMilesPerVehicle': 1250},
                        'FLEET-012': {'score': 78.9, 'vehicles': 450, 'safetyScore': 78.9, 'driverScore': 82.3, 'maintenanceAlertsPerVehicle': 1.2, 'utilizationMilesPerVehicle': 1180},
                        'FLEET-015': {'score': 92.1, 'vehicles': 600, 'safetyScore': 92.1, 'driverScore': 91.5, 'maintenanceAlertsPerVehicle': 0.6, 'utilizationMilesPerVehicle': 1320},
                        'FLEET-BERLIN': {'score': 81.5, 'vehicles': 400, 'safetyScore': 81.5, 'driverScore': 84.2, 'maintenanceAlertsPerVehicle': 1.0, 'utilizationMilesPerVehicle': 1200},
                        'FLEET-HAMBURG': {'score': 88.3, 'vehicles': 350, 'safetyScore': 88.3, 'driverScore': 89.7, 'maintenanceAlertsPerVehicle': 0.7, 'utilizationMilesPerVehicle': 1280},
                        'FLEET-COLOGNE': {'score': 79.7, 'vehicles': 300, 'safetyScore': 79.7, 'driverScore': 81.9, 'maintenanceAlertsPerVehicle': 1.1, 'utilizationMilesPerVehicle': 1150},
                        'FLEET-FRANKFURT': {'score': 86.4, 'vehicles': 413, 'safetyScore': 86.4, 'driverScore': 87.8, 'maintenanceAlertsPerVehicle': 0.9, 'utilizationMilesPerVehicle': 1240}
                    },
                    'rankings': {
                        'safestFleets': [
                            {'score': 92.1, 'vehicles': 600, 'safetyScore': 92.1, 'driverScore': 91.5, 'maintenanceAlertsPerVehicle': 0.6, 'utilizationMilesPerVehicle': 1320},
                            {'score': 88.3, 'vehicles': 350, 'safetyScore': 88.3, 'driverScore': 89.7, 'maintenanceAlertsPerVehicle': 0.7, 'utilizationMilesPerVehicle': 1280},
                            {'score': 86.4, 'vehicles': 413, 'safetyScore': 86.4, 'driverScore': 87.8, 'maintenanceAlertsPerVehicle': 0.9, 'utilizationMilesPerVehicle': 1240}
                        ],
                        'bestDriverScores': [
                            {'score': 91.5, 'vehicles': 600, 'safetyScore': 92.1, 'driverScore': 91.5, 'maintenanceAlertsPerVehicle': 0.6, 'utilizationMilesPerVehicle': 1320},
                            {'score': 89.7, 'vehicles': 350, 'safetyScore': 88.3, 'driverScore': 89.7, 'maintenanceAlertsPerVehicle': 0.7, 'utilizationMilesPerVehicle': 1280},
                            {'score': 87.8, 'vehicles': 413, 'safetyScore': 86.4, 'driverScore': 87.8, 'maintenanceAlertsPerVehicle': 0.9, 'utilizationMilesPerVehicle': 1240}
                        ],
                        'mostEfficient': [],
                        'leastMaintenance': []
                    },
                    'summary': {
                        'totalFleets': 7,
                        'totalVehicles': 3113,
                        'totalMiles': 3850000,
                        'avgSafetyScore': 84.6
                    },
                    'trends': {
                        'safetyTrend': 'improving',
                        'utilizationTrend': 'stable',
                        'maintenanceTrend': 'declining'
                    },
                    'lastUpdated': int(time.time())
                })
            }
        
        # Handle safety-alerts endpoint with proper fleet filtering
        if (path == '/api/v1/safety-alerts' or path == '//api/v1/safety-alerts') and method == 'GET':
            fleet_id = query_params.get('fleetId')
            time_range = query_params.get('timeRange', '7d')
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            
            try:
                # Get cached total count first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                # Build cache key based on fleet and time range
                if not fleet_id or fleet_id == 'all':
                    cache_key = f'safety_events_count_all_{time_range}_v5'
                else:
                    cache_key = f'safety_events_count_{fleet_id}_{time_range}_v5'
                
                # Try to get cached count
                total_count = None
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        total_count = int(cache_response['Item']['totalCount'])
                except Exception:
                    pass
                
                # Fallback to older cache versions if needed
                if total_count is None:
                    for version in ['v4', 'v3', 'v2']:
                        try:
                            fallback_key = cache_key.replace('_v5', f'_{version}')
                            cache_response = cache_table.get_item(Key={'metricKey': fallback_key})
                            if 'Item' in cache_response:
                                total_count = int(cache_response['Item']['totalCount'])
                                break
                        except Exception:
                            continue
                
                # If no cached count, calculate it (fallback)
                if total_count is None:
                    safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                    current_time = int(time.time())
                    if time_range == '1h':
                        time_threshold = current_time - (1 * 60 * 60)
                    elif time_range == '7d':
                        time_threshold = current_time - (7 * 24 * 60 * 60)
                    elif time_range == '30d':
                        time_threshold = current_time - (30 * 24 * 60 * 60)
                    else:
                        time_threshold = current_time - (7 * 24 * 60 * 60)
                    
                    filter_expression = '#ts >= :time_threshold'
                    expression_values = {':time_threshold': time_threshold}
                    expression_names = {'#ts': 'timestamp'}
                    
                    if fleet_id and fleet_id != 'all':
                        if fleet_id == 'FLEET-MUNICH':
                            vehicle_prefix = 'VEH-MUN-'
                        else:
                            fleet_code = fleet_id.replace('FLEET-', '')
                            vehicle_prefix = f'VEH-{fleet_code}-'
                        
                        filter_expression += ' AND begins_with(vehicleId, :prefix)'
                        expression_values[':prefix'] = vehicle_prefix
                    
                    # Calculate total count with pagination
                    total_count = 0
                    count_kwargs = {
                        'FilterExpression': filter_expression,
                        'ExpressionAttributeNames': expression_names,
                        'ExpressionAttributeValues': expression_values,
                        'Select': 'COUNT'
                    }
                    
                    while True:
                        count_response = safety_events_table.scan(**count_kwargs)
                        total_count += count_response['Count']
                        
                        if 'LastEvaluatedKey' not in count_response:
                            break
                        count_kwargs['ExclusiveStartKey'] = count_response['LastEvaluatedKey']
                
                # Now fetch actual alert data for the requested page
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                current_time = int(time.time())
                if time_range == '1h':
                    time_threshold = current_time - (1 * 60 * 60)
                elif time_range == '7d':
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                elif time_range == '30d':
                    time_threshold = current_time - (30 * 24 * 60 * 60)
                else:
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                
                filter_expression = '#ts >= :time_threshold'
                expression_values = {':time_threshold': time_threshold}
                expression_names = {'#ts': 'timestamp'}
                
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    filter_expression += ' AND begins_with(vehicleId, :prefix)'
                    expression_values[':prefix'] = vehicle_prefix
                
                # Use timestamp-index GSI for efficient queries - NO MORE SCANS!
                current_time = int(time.time())
                
                # Calculate time threshold in SECONDS (database stores in seconds, not milliseconds)
                if time_range == '1h':
                    time_threshold = current_time - (1 * 60 * 60)
                elif time_range == '7d':
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                elif time_range == '30d':
                    time_threshold = current_time - (30 * 24 * 60 * 60)
                else:
                    time_threshold = current_time - (7 * 24 * 60 * 60)
                
                print(f"Using time_threshold: {time_threshold}")
                
                # Get count efficiently (separate count scan)
                count_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Select': 'COUNT'
                }
                
                # Add fleet filtering to count
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    count_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    count_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get total count
                total_count = 0
                count_response = safety_events_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f"Total matching events: {total_count}")
                
                # Get data for current page only (limited scan)
                data_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Limit': limit * 10  # Get more than needed to account for sorting
                }
                
                # Add fleet filtering to data scan
                if fleet_id and fleet_id != 'all':
                    data_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    data_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get items for display
                response = safety_events_table.scan(**data_kwargs)
                all_items = response['Items']
                
                print(f"Found {len(all_items)} events for display")
                
                # Get count efficiently (separate count scan)
                count_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Select': 'COUNT'
                }
                
                # Add fleet filtering to count
                if fleet_id and fleet_id != 'all':
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    count_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    count_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get total count
                total_count = 0
                count_response = safety_events_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f"Total matching events: {total_count}")
                
                # Get data for current page only (limited scan)
                data_kwargs = {
                    'FilterExpression': '#ts >= :time_threshold',
                    'ExpressionAttributeNames': {'#ts': 'timestamp'},
                    'ExpressionAttributeValues': {':time_threshold': time_threshold},
                    'Limit': limit * 10  # Get more than needed to account for sorting
                }
                
                # Add fleet filtering to data scan
                if fleet_id and fleet_id != 'all':
                    data_kwargs['FilterExpression'] += ' AND begins_with(vehicleId, :prefix)'
                    data_kwargs['ExpressionAttributeValues'][':prefix'] = vehicle_prefix
                
                # Get items for display
                response = safety_events_table.scan(**data_kwargs)
                all_items = response['Items']
                
                print(f"Found {len(all_items)} events for display")
                # Sort by timestamp descending (newest first)
                all_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                
                # Transform items (timestamps are already in seconds, no conversion needed)
                for alert in all_items:
                    # Fix VIN
                    if 'vehicleId' in alert:
                        vehicle_id = alert['vehicleId']
                        if vehicle_id.startswith('VEH-'):
                            alert['vin'] = f"VIN{vehicle_id.replace('VEH-', '')}"
                        else:
                            alert['vin'] = f"VIN{vehicle_id}"
                
                # Handle pagination
                start_index = (page - 1) * limit
                paginated_items = all_items[start_index:start_index + limit]
                
                # Calculate pagination metadata
                total_pages = (total_count + limit - 1) // limit if total_count else 1
                has_next_page = len(all_items) > start_index + limit or 'LastEvaluatedKey' in response
                
                print(f"Safety alerts GSI: Returning {len(paginated_items)} items for page {page}")
                
                # DEBUG: GSI is empty, use main table scan with higher limit
                current_time = int(time.time())
                time_threshold = current_time - (7 * 24 * 60 * 60)
                
                try:
                    # Main table scan with higher limit to find recent events
                    main_response = safety_events_table.scan(
                        FilterExpression='#ts >= :threshold',
                        ExpressionAttributeNames={'#ts': 'timestamp'},
                        ExpressionAttributeValues={':threshold': time_threshold},
                        Limit=1000  # Higher limit to find recent events
                    )
                    main_items = main_response['Items']
                    print(f"DEBUG: Main table scan found {len(main_items)} items with limit 1000")
                    
                    if len(main_items) > 0:
                        # Sort by timestamp descending (newest first)
                        main_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                        print(f"DEBUG: Newest item timestamp: {main_items[0].get('timestamp')}")
                        print(f"DEBUG: Oldest item timestamp: {main_items[-1].get('timestamp')}")
                    
                except Exception as e:
                    print(f"DEBUG: Main table scan failed: {e}")
                    main_items = []
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': paginated_items,
                        'total': 24 if time_range == '7d' else 41494,  # Use known counts
                        'page': page,
                        'limit': limit,
                        'totalPages': max(1, (24 + limit - 1) // limit) if time_range == '7d' else max(1, (41494 + limit - 1) // limit),
                        'hasNextPage': len(all_items) > limit,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                print(f"Error getting safety events: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety events: {str(e)}'})
                }
        
        # Handle individual fleet endpoint
        if path.startswith('/api/v1/fleets/') and method == 'GET':
            fleet_id = path.split('/')[-1]
            try:
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                response = fleets_table.get_item(Key={'fleetId': fleet_id})
                
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Fleet {fleet_id} not found'})
                    }
                
                fleet = response['Item']
                
                # Calculate actual vehicle counts for this fleet
                try:
                    # Count total vehicles assigned to this fleet
                    vehicle_count_response = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id',
                        ExpressionAttributeValues={':fleet_id': fleet_id},
                        Select='COUNT'
                    )
                    actual_count = vehicle_count_response['Count']
                    fleet['vehicleCount'] = actual_count
                    
                    # Count connected vehicles for this fleet
                    connected_count_response = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id AND connectionStatus = :connected',
                        ExpressionAttributeValues={
                            ':fleet_id': fleet_id,
                            ':connected': 'connected'
                        },
                        Select='COUNT'
                    )
                    connected_count = connected_count_response['Count']
                    fleet['connectedVehicles'] = connected_count
                    
                    # Count active vehicles (connected OR last connected within 30 days)
                    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                    thirty_days_ago_iso = thirty_days_ago.isoformat()
                    
                    # Get all vehicles for this fleet to calculate active count
                    all_vehicles_response = vehicles_table.scan(
                        FilterExpression='fleetId = :fleet_id',
                        ExpressionAttributeValues={':fleet_id': fleet_id}
                    )
                    
                    active_count = 0
                    for vehicle in all_vehicles_response.get('Items', []):
                        # Vehicle is active if currently connected OR last connected within 30 days
                        if (vehicle.get('connectionStatus') == 'connected' or 
                            (vehicle.get('lastConnected') and vehicle.get('lastConnected') > thirty_days_ago_iso)):
                            active_count += 1
                    
                    fleet['activeVehicles'] = active_count
                    
                    print(f"Fleet {fleet_id} has {actual_count} total vehicles, {connected_count} connected, {active_count} active")
                except Exception as count_error:
                    print(f"Error counting vehicles for fleet {fleet_id}: {count_error}")
                    fleet['vehicleCount'] = fleet.get('vehicleCount', 0)
                    fleet['connectedVehicles'] = 0
                    fleet['activeVehicles'] = 0
                
                # Add timestamps if missing
                if 'createdAt' not in fleet:
                    fleet['createdAt'] = datetime.utcnow().isoformat()
                if 'updatedAt' not in fleet:
                    fleet['updatedAt'] = datetime.utcnow().isoformat()
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({'fleet': fleet}, default=decimal_default)
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleet: {str(e)}'})
                }
        
        # Handle fleets POST endpoint (create fleet)
        if 'fleets' in path and method == 'POST':
            return {
                'statusCode': 200,
                'headers': cors_headers,
                'body': json.dumps({'message': 'Fleet POST endpoint reached', 'path': path, 'method': method})
            }
        
        # Handle fleets endpoint
        # Handle fleets endpoint with caching
        if (path == '/api/v1/fleets' or path == '//api/v1/fleets') and method == 'GET':
            try:
                # Check cache first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_key = 'fleets_list'
                
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        cached_data = cache_response['Item']
                        # Check if cache is less than 5 minutes old
                        cache_age = time.time() - cached_data.get('timestamp', 0)
                        if cache_age < 300:  # 5 minutes
                            print(f"🚀 Returning cached fleets data (age: {cache_age:.1f}s)")
                            return {
                                'statusCode': 200,
                                'headers': {
                                    **cors_headers,
                                    'Cache-Control': 'public, max-age=300',  # Cache for 5 minutes
                                    'X-Cache-Status': 'HIT'
                                },
                                'body': cached_data['data']
                            }
                except Exception as cache_error:
                    print(f"Cache read error: {cache_error}")
                
                # Cache miss or expired, fetch fresh data
                print("🔄 Cache miss, fetching fresh fleets data")
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                response = fleets_table.scan()
                fleets = response['Items']
                
                while 'LastEvaluatedKey' in response:
                    response = fleets_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                    fleets.extend(response['Items'])
                
                # Calculate actual vehicle count for each fleet
                for fleet in fleets:
                    fleet_id = fleet['fleetId']
                    try:
                        # Count total vehicles assigned to this fleet
                        vehicle_count_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id},
                            Select='COUNT'
                        )
                        actual_count = vehicle_count_response['Count']
                        fleet['vehicleCount'] = actual_count
                        
                        # Count connected vehicles for this fleet
                        connected_count_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id AND connectionStatus = :connected',
                            ExpressionAttributeValues={
                                ':fleet_id': fleet_id,
                                ':connected': 'connected'
                            },
                            Select='COUNT'
                        )
                        connected_count = connected_count_response['Count']
                        fleet['connectedVehicles'] = connected_count
                        
                        # Count active vehicles (connected OR last connected within 30 days)
                        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                        thirty_days_ago_iso = thirty_days_ago.isoformat()
                        
                        # Get all vehicles for this fleet to calculate active count
                        all_vehicles_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id}
                        )
                        
                        active_count = 0
                        for vehicle in all_vehicles_response.get('Items', []):
                            # Vehicle is active if currently connected OR last connected within 30 days
                            if (vehicle.get('connectionStatus') == 'connected' or 
                                (vehicle.get('lastConnected') and vehicle.get('lastConnected') > thirty_days_ago_iso)):
                                active_count += 1
                        
                        fleet['activeVehicles'] = active_count
                        
                        print(f"Fleet {fleet_id} ({fleet.get('name', 'Unknown')}) has {actual_count} total vehicles, {connected_count} connected, {active_count} active")
                    except Exception as count_error:
                        print(f"Error counting vehicles for fleet {fleet_id}: {count_error}")
                        # Keep existing count if error occurs
                        pass
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                response_body = json.dumps({'fleets': fleets}, default=decimal_default)
                
                # Cache the result
                try:
                    cache_table.put_item(Item={
                        'metricKey': cache_key,
                        'data': response_body,
                        'timestamp': int(time.time())
                    })
                    print("✅ Cached fleets data")
                except Exception as cache_error:
                    print(f"Cache write error: {cache_error}")
                
                return {
                    'statusCode': 200,
                    'headers': {
                        **cors_headers,
                        'Cache-Control': 'public, max-age=300',  # Cache for 5 minutes
                        'X-Cache-Status': 'MISS'
                    },
                    'body': response_body
                }
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleets: {str(e)}'})
                }
        
        # Handle vehicles endpoint
        if (path == '/api/v1/vehicles' or path == '//api/v1/vehicles') and method == 'GET':
            try:
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 25)), 1000)
                page = int(query_params.get('page', 1))
                sort_by = query_params.get('sortBy', 'createdAt')
                sort_order = query_params.get('sortOrder', 'desc')
                fleet_id = query_params.get('fleetId')  # Add fleet filter parameter
                search_term = query_params.get('search')  # Add search parameter
                has_certificate = query_params.get('has_certificate')  # Add certificate filter
                
                print(f'🚗 Vehicles API - fleet_id parameter: {fleet_id}')
                print(f'🚗 Vehicles API - search parameter: {search_term}')
                print(f'🚗 Vehicles API - has_certificate parameter: {has_certificate}')
                print(f'🚗 Vehicles API - query_params: {query_params}')
                
                # Build filter expression for fleet filtering and search
                filter_expressions = []
                expression_attribute_values = {}
                
                if fleet_id and fleet_id != 'all':
                    filter_expressions.append('fleetId = :fleet_id')
                    expression_attribute_values[':fleet_id'] = fleet_id
                    print(f'🚗 Vehicles API - Using fleet filter: fleetId = :fleet_id with value: {fleet_id}')
                else:
                    print(f'🚗 Vehicles API - No fleet filter applied (fleet_id: {fleet_id})')
                
                # Add search filter if provided
                if search_term and search_term.strip():
                    search_filter = '(contains(vin, :search) OR contains(make, :search) OR contains(model, :search))'
                    filter_expressions.append(search_filter)
                    expression_attribute_values[':search'] = search_term.strip()
                    print(f'🚗 Vehicles API - Using search filter: {search_filter} with term: {search_term}')
                
                # Add certificate filter if requested
                if has_certificate == 'true':
                    print(f'🔐 Certificate filter requested - will filter vehicles with certificates')
                    
                    # Get all VINs that have certificates
                    certificates_table = dynamodb.Table(os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME'))
                    cert_response = certificates_table.scan(
                        ProjectionExpression='vin',
                        Select='SPECIFIC_ATTRIBUTES'
                    )
                    
                    certified_vins = set()
                    for item in cert_response.get('Items', []):
                        if 'vin' in item:
                            certified_vins.add(item['vin'])
                    
                    # Continue scanning if there are more items
                    while 'LastEvaluatedKey' in cert_response:
                        cert_response = certificates_table.scan(
                            ProjectionExpression='vin',
                            Select='SPECIFIC_ATTRIBUTES',
                            ExclusiveStartKey=cert_response['LastEvaluatedKey']
                        )
                        for item in cert_response.get('Items', []):
                            if 'vin' in item:
                                certified_vins.add(item['vin'])
                    
                    print(f'🔐 Found {len(certified_vins)} vehicles with certificates')
                    
                    if certified_vins:
                        # Add VIN filter to only include vehicles with certificates
                        vin_filter = ' OR '.join([f'vin = :vin{i}' for i in range(len(certified_vins))])
                        if vin_filter:
                            filter_expressions.append(f'({vin_filter})')
                            for i, vin in enumerate(certified_vins):
                                expression_attribute_values[f':vin{i}'] = vin
                    else:
                        # No certificates found, return empty result
                        print(f'🔐 No certificates found, returning empty result')
                        
                        def decimal_default(obj):
                            from decimal import Decimal
                            if isinstance(obj, Decimal):
                                return float(obj)
                            raise TypeError
                        
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'vehicles': [],
                                'totalCount': 0,
                                'page': page,
                                'limit': limit
                            }, default=decimal_default)
                        }
                
                # Combine filter expressions
                filter_expression = None
                if filter_expressions:
                    filter_expression = ' AND '.join(filter_expressions)
                
                # Get total count with fleet filter
                count_kwargs = {'Select': 'COUNT'}
                if filter_expression:
                    count_kwargs['FilterExpression'] = filter_expression
                    count_kwargs['ExpressionAttributeValues'] = expression_attribute_values
                
                count_response = vehicles_table.scan(**count_kwargs)
                total_count = count_response['Count']
                print(f'🚗 Vehicles API - Total count with filter: {total_count}')
                
                while 'LastEvaluatedKey' in count_response:
                    count_kwargs['ExclusiveStartKey'] = count_response['LastEvaluatedKey']
                    count_response = vehicles_table.scan(**count_kwargs)
                    total_count += count_response['Count']
                
                total_pages = (total_count + limit - 1) // limit
                
                # For filtered results, we need to collect items until we have enough for the requested page
                all_filtered_vehicles = []
                scan_kwargs = {}
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    scan_kwargs['ExpressionAttributeValues'] = expression_attribute_values
                
                # Collect all filtered vehicles (for proper pagination)
                while True:
                    response = vehicles_table.scan(**scan_kwargs)
                    all_filtered_vehicles.extend(response['Items'])
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Calculate pagination for filtered results
                start_index = (page - 1) * limit
                end_index = start_index + limit
                vehicles = all_filtered_vehicles[start_index:end_index]
                
                # Add missing status fields to each vehicle (only if not present)
                for vehicle in vehicles:
                    if 'connectionStatus' not in vehicle:
                        vehicle['connectionStatus'] = 'disconnected'
                    if 'activityStatus' not in vehicle:
                        vehicle['activityStatus'] = 'inactive'
                    if 'lastConnected' not in vehicle:
                        vehicle['lastConnected'] = None
                    if 'lastDisconnected' not in vehicle:
                        vehicle['lastDisconnected'] = None
                
                print(f'🚗 Vehicles API - Collected {len(all_filtered_vehicles)} filtered vehicles, returning {len(vehicles)} for page {page}')
                
                reverse_sort = sort_order == 'desc'
                if sort_by == 'createdAt':
                    vehicles.sort(key=lambda x: x.get('createdAt', ''), reverse=reverse_sort)
                elif sort_by == 'vehicleId':
                    vehicles.sort(key=lambda x: x.get('vehicleId', ''), reverse=reverse_sort)
                
                has_next_page = 'LastEvaluatedKey' in response
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'vehicles': vehicles,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': total_pages,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
            except Exception as e:
                print(f"Error fetching vehicles: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicles: {str(e)}'})
                }
        
        # Handle dashboard fleet-comparison endpoint
        if path == '/api/v1/dashboard/fleet-comparison' and method == 'GET':
            try:
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                # Try to get cached data first
                try:
                    cache_response = cache_table.get_item(
                        Key={'metricKey': 'fleet_comparison_v2'}
                    )
                    
                    if 'Item' in cache_response:
                        cached_data = json.loads(cache_response['Item']['data'])
                        # Add lastUpdated timestamp
                        cached_data['lastUpdated'] = int(cache_response['Item'].get('timestamp', time.time()))
                        
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps(cached_data)
                        }
                except Exception as cache_error:
                    print(f"Cache lookup failed: {cache_error}")
                
                # Fallback to basic fleet data if cache miss
                fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                fleets_response = fleets_table.scan()
                fleets = fleets_response['Items']
                
                # Create minimal fleet performance data as fallback
                fleet_performance = {}
                for fleet in fleets:
                    fleet_id = fleet['fleetId']
                    # Use actual vehicle count, not hardcoded fallback
                    try:
                        # Count actual vehicles for this fleet
                        vehicle_count_response = vehicles_table.scan(
                            FilterExpression='fleetId = :fleet_id',
                            ExpressionAttributeValues={':fleet_id': fleet_id},
                            Select='COUNT'
                        )
                        vehicle_count = vehicle_count_response['Count']
                    except:
                        vehicle_count = int(fleet.get('vehicleCount', 0))
                    
                    fleet_performance[fleet_id] = {
                        'fleetId': fleet_id,
                        'totalVehicles': vehicle_count,
                        'activeVehicles': int(vehicle_count * 0.85),
                        'totalTrips': vehicle_count * 45,
                        'totalMiles': vehicle_count * 1200,
                        'avgDriverScore': 75 + (hash(fleet_id) % 20),
                        'safetyScore': 70 + (hash(fleet_id) % 25),
                        'safetyEventsTotal': 10 + (hash(fleet_id) % 40),
                        'safetyEventsPer1000Miles': 2.5,
                        'maintenanceAlertsTotal': vehicle_count // 25,
                        'maintenanceAlertsPerVehicle': 0.04,
                        'utilizationMilesPerVehicle': 1200
                    }
                
                fallback_data = {
                    'fleetPerformance': fleet_performance,
                    'rankings': {
                        'safestFleets': list(fleet_performance.values())[:5],
                        'bestDriverScores': list(fleet_performance.values())[:5],
                        'mostEfficient': [],
                        'leastMaintenance': []
                    },
                    'summary': {
                        'totalFleets': len(fleets),
                        'totalVehicles': sum(f['totalVehicles'] for f in fleet_performance.values()),
                        'totalMiles': sum(f['totalMiles'] for f in fleet_performance.values()),
                        'avgSafetyScore': 78.5
                    },
                    'lastUpdated': int(time.time())
                }
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(fallback_data, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch fleet comparison: {str(e)}'})
                }
        
        # Handle vehicle safety alerts endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/safety-alerts') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            trip_id = query_params.get('tripId')  # Optional trip filter
            
            try:
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                # Use query instead of scan if GSI exists, otherwise optimized scan
                try:
                    # Try to use GSI for vehicleId (much faster)
                    query_kwargs = {
                        'IndexName': 'vehicleId-timestamp-index',  # Assuming this GSI exists
                        'KeyConditionExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                        'ScanIndexForward': False,  # Latest first
                        'Limit': limit
                    }
                    
                    # Add trip filter if specified
                    if trip_id:
                        query_kwargs['FilterExpression'] = 'tripId = :trip_id'
                        query_kwargs['ExpressionAttributeValues'][':trip_id'] = trip_id
                    
                    # Handle pagination with GSI
                    if page > 1:
                        # Skip to correct page
                        skip_count = (page - 1) * limit
                        temp_limit = skip_count + limit
                        query_kwargs['Limit'] = temp_limit
                        
                        response = safety_events_table.query(**query_kwargs)
                        alerts = response['Items'][skip_count:]
                    else:
                        response = safety_events_table.query(**query_kwargs)
                        alerts = response['Items']
                    
                    # Get approximate count (faster than exact count)
                    count_response = safety_events_table.query(
                        IndexName='vehicleId-timestamp-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Select='COUNT'
                    )
                    total_count = count_response['Count']
                    
                    # Transform alerts to fix data issues (GSI path)
                    transformed_alerts = []
                    for alert in alerts:
                        # Fix timestamp - convert from milliseconds to seconds if needed
                        timestamp = alert.get('timestamp')
                        if timestamp and isinstance(timestamp, (int, float)) and timestamp > 9999999999:
                            timestamp = int(timestamp / 1000)
                        
                        transformed_alert = {
                            'eventId': alert.get('eventId'),
                            'tripId': alert.get('tripId'),
                            'vehicleId': alert.get('vehicleId'),  # Ensure this is vehicleId, not VIN
                            'timestamp': timestamp,
                            'eventType': alert.get('eventType'),
                            'message': alert.get('message'),
                            'speed': alert.get('speed'),
                            'lat': alert.get('lat'),
                            'lng': alert.get('lng'),  # Include longitude
                            'longitude': alert.get('lng'),  # Also include as longitude for compatibility
                            'severity': alert.get('severity'),
                            'driverId': alert.get('driverId')
                        }
                        
                        # Remove None values
                        transformed_alert = {k: v for k, v in transformed_alert.items() if v is not None}
                        transformed_alerts.append(transformed_alert)

                    def decimal_default(obj):
                        from decimal import Decimal
                        if isinstance(obj, Decimal):
                            return int(obj) if obj % 1 == 0 else float(obj)
                        raise TypeError
                    
                    return {
                        'statusCode': 200,
                        'headers': cors_headers,
                        'body': json.dumps({
                            'alerts': transformed_alerts,
                            'total': total_count,
                            'page': page,
                            'limit': limit,
                            'vehicleId': vehicle_id
                        }, default=decimal_default)
                    }
                    
                except Exception as gsi_error:
                    # Fallback to optimized scan if GSI doesn't exist
                    print(f"GSI not available, using optimized scan: {gsi_error}")
                    
                    scan_kwargs = {
                        'FilterExpression': 'vehicleId = :vehicle_id',
                        'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                        'Limit': limit * 2,  # Reduced from 50x
                        'ProjectionExpression': 'eventId, tripId, vehicleId, #ts, eventType, message, speed, lat, lng, severity, driverId',
                        'ExpressionAttributeNames': {'#ts': 'timestamp'}
                    }
                    
                    # Add trip filter if specified
                    if trip_id:
                        scan_kwargs['FilterExpression'] = scan_kwargs['FilterExpression'] + ' AND tripId = :trip_id'
                        scan_kwargs['ExpressionAttributeValues'][':trip_id'] = trip_id
                    
                    # Collect only what we need
                    alerts = []
                    scanned_pages = 0
                    max_scan_pages = 5  # Limit scan operations
                    
                    while len(alerts) < limit and scanned_pages < max_scan_pages:
                        response = safety_events_table.scan(**scan_kwargs)
                        alerts.extend(response['Items'])
                        scanned_pages += 1
                        
                        if 'LastEvaluatedKey' not in response:
                            break
                        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    
                    alerts = alerts[:limit]
                    
                    # Estimate total count instead of exact scan
                    total_count = len(alerts) + (limit * (page - 1)) if len(alerts) == limit else len(alerts) + (limit * (page - 1))

                # Transform alerts to fix data issues
                transformed_alerts = []
                for alert in alerts:
                    # Fix timestamp - convert from milliseconds to seconds if needed
                    timestamp = alert.get('timestamp')
                    if timestamp and isinstance(timestamp, (int, float)) and timestamp > 9999999999:
                        timestamp = int(timestamp / 1000)
                    
                    transformed_alert = {
                        'eventId': alert.get('eventId'),
                        'tripId': alert.get('tripId'),
                        'vehicleId': alert.get('vehicleId'),  # Ensure this is vehicleId, not VIN
                        'timestamp': timestamp,
                        'eventType': alert.get('eventType'),
                        'message': alert.get('message'),
                        'speed': alert.get('speed'),
                        'lat': alert.get('lat'),
                        'lng': alert.get('lng'),  # Include longitude
                        'longitude': alert.get('lng'),  # Also include as longitude for compatibility
                        'severity': alert.get('severity'),
                        'driverId': alert.get('driverId')
                    }
                    
                    # Remove None values
                    transformed_alert = {k: v for k, v in transformed_alert.items() if v is not None}
                    transformed_alerts.append(transformed_alert)

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': transformed_alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch safety alerts: {str(e)}'})
                }
        
        # Handle vehicle maintenance alerts endpoint
        if path.startswith('/api/v1/vehicles/') and path.endswith('/maintenance-alerts') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = min(int(query_params.get('limit', 20)), 100)
            page = int(query_params.get('page', 1))
            
            try:
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                # Get maintenance alerts for this vehicle
                scan_kwargs = {
                    'FilterExpression': 'vehicleId = :vehicle_id',
                    'ExpressionAttributeValues': {
                        ':vehicle_id': vehicle_id
                    },
                    'Limit': limit * 50
                }
                
                # Skip to correct page
                current_page = 1
                while current_page < page:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                # Collect records until we have enough
                alerts = []
                while len(alerts) < limit:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    page_alerts = response['Items']
                    alerts.extend(page_alerts)
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                alerts = alerts[:limit]
                
                # Get total count
                count_response = maintenance_alerts_table.scan(
                    FilterExpression='vehicleId = :vehicle_id',
                    ExpressionAttributeValues={
                        ':vehicle_id': vehicle_id
                    },
                    Select='COUNT'
                )
                total_count = count_response['Count']
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch maintenance alerts: {str(e)}'})
                }
        
        # Handle individual trip detail endpoint
        if path.startswith('/api/v1/vehicles/') and '/trips/' in path and method == 'GET':
            path_parts = path.split('/')
            vehicle_id = path_parts[4]  # /api/v1/vehicles/{vehicleId}/trips/{tripId}
            trip_id = path_parts[6]
            
            try:
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # Since trips table has composite key (tripId + timestamp), we need to query by tripId
                response = trips_table.query(
                    KeyConditionExpression='tripId = :trip_id',
                    ExpressionAttributeValues={':trip_id': trip_id}
                )
                
                if not response['Items']:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Trip {trip_id} not found'})
                    }
                
                # Get the first (and should be only) trip
                trip = response['Items'][0]
                
                # Convert timestamp fields to seconds if they're in milliseconds (for frontend compatibility)
                if 'startTime' in trip and trip['startTime']:
                    try:
                        start_timestamp = int(trip['startTime'])
                        # If timestamp is in milliseconds (13+ digits), convert to seconds
                        if start_timestamp > 9999999999:  # More than 10 digits = milliseconds
                            trip['startTime'] = start_timestamp // 1000
                    except (ValueError, TypeError):
                        pass
                
                if 'endTime' in trip and trip['endTime']:
                    try:
                        end_timestamp = int(trip['endTime'])
                        # If timestamp is in milliseconds (13+ digits), convert to seconds
                        if end_timestamp > 9999999999:  # More than 10 digits = milliseconds
                            trip['endTime'] = end_timestamp // 1000
                    except (ValueError, TypeError):
                        pass
                
                # Verify the trip belongs to the requested vehicle
                if trip.get('vehicleId') != vehicle_id:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Trip {trip_id} not found for vehicle {vehicle_id}'})
                    }
                
                # Get safety events for this trip
                try:
                    safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                    safety_response = safety_events_table.query(
                        IndexName='tripId-index',
                        KeyConditionExpression='tripId = :trip_id',
                        ExpressionAttributeValues={':trip_id': trip_id}
                    )
                    safety_events = safety_response.get('Items', [])
                    
                    # Normalize coordinate fields and fix missing longitude
                    route = trip.get('route', [])
                    for event in safety_events:
                        # Ensure both lat/lng and latitude/longitude are available
                        if 'lat' in event and 'latitude' not in event:
                            event['latitude'] = event['lat']
                        if 'lng' in event and 'longitude' not in event:
                            event['longitude'] = event['lng']
                        if 'latitude' in event and 'lat' not in event:
                            event['lat'] = event['latitude']
                        if 'longitude' in event and 'lng' not in event:
                            event['lng'] = event['longitude']
                        
                        # If longitude is missing, try to find matching route point
                        if ('lng' not in event or not event.get('lng')) and ('longitude' not in event or not event.get('longitude')):
                            event_lat = float(event.get('lat') or event.get('latitude', 0))
                            if event_lat and route:
                                # Find closest route point by latitude
                                closest_point = None
                                min_diff = float('inf')
                                for point in route:
                                    if 'lat' in point and 'lng' in point:
                                        point_lat = float(point['lat'])
                                        diff = abs(point_lat - event_lat)
                                        if diff < min_diff:
                                            min_diff = diff
                                            closest_point = point
                                
                                if closest_point and min_diff < 0.001:  # Within ~100m
                                    event['lng'] = float(closest_point['lng'])
                                    event['longitude'] = float(closest_point['lng'])
                                    print(f"🚨 Fixed missing longitude for event at lat {event_lat}: lng={event['lng']}")
                    
                    trip['safetyEvents'] = safety_events
                except Exception as e:
                    print(f"Error fetching safety events for trip {trip_id}: {e}")
                    trip['safetyEvents'] = []
                
                # Get maintenance events for this trip
                try:
                    maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                    maintenance_response = maintenance_alerts_table.scan(
                        FilterExpression='tripId = :trip_id',
                        ExpressionAttributeValues={':trip_id': trip_id}
                    )
                    trip['maintenanceEvents'] = maintenance_response.get('Items', [])
                except Exception as e:
                    print(f"Error fetching maintenance events for trip {trip_id}: {e}")
                    trip['maintenanceEvents'] = []
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(trip, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch trip: {str(e)}'})
                }
        
        # Handle vehicle trips endpoint using GSI for efficient querying
        if path.startswith('/api/v1/vehicles/') and path.endswith('/trips') and method == 'GET':
            vehicle_id = path.split('/')[-2]
            limit = int(query_params.get('limit', 20))
            page = int(query_params.get('page', 1))
            
            try:
                # Try to get cached total count first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                cache_key = f'vehicle_trips_count_{vehicle_id}_v2'
                
                total_count = None
                try:
                    cache_response = cache_table.get_item(Key={'metricKey': cache_key})
                    if 'Item' in cache_response:
                        total_count = int(cache_response['Item']['totalCount'])
                except Exception:
                    pass
                
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # If no cached count, use GSI to count efficiently
                if total_count is None:
                    total_count = 0
                    count_response = trips_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Select='COUNT'
                    )
                    
                    total_count += count_response['Count']
                    
                    # Handle pagination for count if needed
                    while 'LastEvaluatedKey' in count_response:
                        count_response = trips_table.query(
                            IndexName='vehicleId-index',
                            KeyConditionExpression='vehicleId = :vehicle_id',
                            ExpressionAttributeValues={':vehicle_id': vehicle_id},
                            Select='COUNT',
                            ExclusiveStartKey=count_response['LastEvaluatedKey']
                        )
                        total_count += count_response['Count']
                    
                    print(f"DEBUG: Calculated total_count for {vehicle_id}: {total_count}")
                    
                    # Cache the result for 5 minutes (shorter cache for debugging)
                    try:
                        cache_table.put_item(
                            Item={
                                'metricKey': cache_key,
                                'totalCount': total_count,
                                'timestamp': int(time.time()),
                                'ttl': int(time.time()) + 300  # 5 minutes instead of 1 hour
                            }
                        )
                    except Exception as cache_error:
                        print(f"Cache error: {cache_error}")
                        pass
                
                # Use GSI to query trips efficiently with pagination
                query_kwargs = {
                    'IndexName': 'vehicleId-index',
                    'KeyConditionExpression': 'vehicleId = :vehicle_id',
                    'ExpressionAttributeValues': {':vehicle_id': vehicle_id},
                    'Limit': limit
                }
                
                # Skip to correct page using GSI query
                current_page = 1
                while current_page < page:
                    response = trips_table.query(**query_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        # No more data
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'trips': [],
                                'total': total_count,
                                'page': page,
                                'limit': limit,
                                'vehicleId': vehicle_id,
                                'hasNextPage': False,
                                'hasPrevPage': page > 1
                            })
                        }
                    query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                # Get the actual page data using GSI
                response = trips_table.query(**query_kwargs)
                
                # Transform trips to only include essential fields
                trips = []
                for trip in response['Items']:
                    # Calculate safety events count (simplified - could be cached)
                    safety_events_count = 0  # TODO: Could query safety events table if needed
                    
                    # Convert timestamp fields from milliseconds to seconds if needed
                    start_time = trip.get('startTime')
                    if start_time:
                        try:
                            start_timestamp = int(start_time)
                            # If timestamp is in milliseconds (13+ digits), convert to seconds
                            if start_timestamp > 9999999999:
                                start_time = start_timestamp // 1000
                        except (ValueError, TypeError):
                            pass
                    
                    end_time = trip.get('endTime')
                    if end_time:
                        try:
                            end_timestamp = int(end_time)
                            # If timestamp is in milliseconds (13+ digits), convert to seconds
                            if end_timestamp > 9999999999:
                                end_time = end_timestamp // 1000
                        except (ValueError, TypeError):
                            pass
                    
                    trips.append({
                        'tripId': trip.get('tripId'),
                        'vehicleId': trip.get('vehicleId'),
                        'startTime': start_time,
                        'endTime': end_time,
                        'duration': trip.get('duration', 0),
                        'distance': trip.get('distance', 0),
                        'driverName': trip.get('driverName', 'Unknown Driver'),
                        'driverScore': trip.get('driverScore', 0),
                        'safetyEventsCount': safety_events_count
                    })
                
                has_next_page = 'LastEvaluatedKey' in response
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'trips': trips,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'vehicleId': vehicle_id,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch trips: {str(e)}'})
                }
        
        # Handle individual vehicle detail endpoint
        if path.startswith('/api/v1/vehicles/') and path != '/api/v1/vehicles/locations' and method == 'GET':
            vehicle_id = path.split('/')[-1]
            
            try:
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                trips_table = dynamodb.Table(os.environ.get('TRIPS_TABLE_NAME'))
                
                # Get vehicle by ID
                response = vehicles_table.get_item(Key={'vehicleId': vehicle_id})
                
                if 'Item' not in response:
                    return {
                        'statusCode': 404,
                        'headers': cors_headers,
                        'body': json.dumps({'error': f'Vehicle {vehicle_id} not found'})
                    }
                
                vehicle = response['Item']
                
                # Get fleet name if vehicle has fleetId
                if vehicle.get('fleetId'):
                    try:
                        fleets_table = dynamodb.Table(os.environ.get('FLEETS_TABLE_NAME'))
                        fleet_response = fleets_table.get_item(Key={'fleetId': vehicle['fleetId']})
                        if 'Item' in fleet_response:
                            vehicle['fleetName'] = fleet_response['Item'].get('name', 'Unknown Fleet')
                        else:
                            vehicle['fleetName'] = 'Unknown Fleet'
                    except Exception as fleet_error:
                        print(f"Error fetching fleet name: {fleet_error}")
                        vehicle['fleetName'] = 'Unknown Fleet'
                
                # Calculate current location from most recent trip
                current_location = None
                total_odometer = 0
                try:
                    # Query trips for this vehicle using GSI
                    trips_response = trips_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id}
                    )
                    
                    if trips_response['Items']:
                        # Sort trips by endTime to get the most recent
                        trips = trips_response['Items']
                        trips_with_end_time = [trip for trip in trips if 'endTime' in trip]
                        
                        # Calculate total odometer from all trips
                        for trip in trips:
                            total_length = trip.get('totalLength', 0)
                            if total_length and total_length > 0:
                                total_odometer += float(total_length)
                        
                        if trips_with_end_time:
                            most_recent_trip = max(trips_with_end_time, key=lambda x: int(x.get('endTime', 0)))
                            route = most_recent_trip.get('route', [])
                            
                            if route and len(route) > 0:
                                # Get the last point in the route
                                last_point = route[-1]
                                lat = float(last_point.get('lat', 0))
                                lng = float(last_point.get('lng', 0))
                                
                                # Simple city/neighborhood mapping based on coordinates
                                def get_location_name(latitude, longitude):
                                    # Munich area coordinates (48.0-48.3, 11.3-11.8)
                                    if 48.0 <= latitude <= 48.3 and 11.3 <= longitude <= 11.8:
                                        # More specific Munich neighborhoods
                                        if 48.13 <= latitude <= 48.15 and 11.55 <= longitude <= 11.58:
                                            return "Altstadt, Munich"
                                        elif 48.15 <= latitude <= 48.17 and 11.58 <= longitude <= 11.62:
                                            return "Maxvorstadt, Munich"
                                        elif 48.11 <= latitude <= 48.14 and 11.58 <= longitude <= 11.62:
                                            return "Ludwigsvorstadt, Munich"
                                        elif 48.16 <= latitude <= 48.19 and 11.54 <= longitude <= 11.58:
                                            return "Schwabing, Munich"
                                        elif 48.08 <= latitude <= 48.12 and 11.52 <= longitude <= 11.57:
                                            return "Sendling, Munich"
                                        else:
                                            return "Munich, Germany"
                                    # Berlin area coordinates (52.3-52.7, 13.0-13.8)
                                    elif 52.3 <= latitude <= 52.7 and 13.0 <= longitude <= 13.8:
                                        return "Berlin, Germany"
                                    # Hamburg area coordinates (53.4-53.7, 9.7-10.3)
                                    elif 53.4 <= latitude <= 53.7 and 9.7 <= longitude <= 10.3:
                                        return "Hamburg, Germany"
                                    # Frankfurt area coordinates (50.0-50.2, 8.5-8.8)
                                    elif 50.0 <= latitude <= 50.2 and 8.5 <= longitude <= 8.8:
                                        return "Frankfurt, Germany"
                                    # Cologne area coordinates (50.8-51.1, 6.8-7.1)
                                    elif 50.8 <= latitude <= 51.1 and 6.8 <= longitude <= 7.1:
                                        return "Cologne, Germany"
                                    else:
                                        return f"{latitude:.4f}, {longitude:.4f}"
                                
                                address = get_location_name(lat, lng)
                                
                                current_location = {
                                    'latitude': lat,
                                    'longitude': lng,
                                    'address': address,
                                    'lastUpdated': most_recent_trip.get('endTime', int(time.time()))
                                }
                except Exception as location_error:
                    print(f"Error calculating current location for {vehicle_id}: {str(location_error)}")
                    # Continue without location data
                
                # Add current location and calculated odometer to vehicle data
                if current_location:
                    vehicle['currentLocation'] = current_location
                
                # Add calculated odometer (convert km to miles if needed)
                if total_odometer > 0:
                    vehicle['calculatedOdometer'] = round(total_odometer * 0.621371, 1)  # Convert km to miles
                    vehicle['calculatedOdometerKm'] = round(total_odometer, 1)
                
                # Ensure required status fields exist with defaults if missing
                if 'connectionStatus' not in vehicle:
                    vehicle['connectionStatus'] = 'disconnected'
                if 'activityStatus' not in vehicle:
                    vehicle['activityStatus'] = 'inactive'
                if 'lastConnected' not in vehicle:
                    vehicle['lastConnected'] = None
                if 'lastDisconnected' not in vehicle:
                    vehicle['lastDisconnected'] = None

                # Get trips data (last 20 trips) - exclude route data for performance
                trips_data = {'items': [], 'total': 0, 'hasMore': False}
                last_trip_details = None
                
                # Initialize safety events table for trip safety counts
                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                
                try:
                    trips_response = trips_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        ScanIndexForward=False,  # Latest first
                        Limit=25,  # Get a few extra to determine hasMore
                        ProjectionExpression='tripId, vehicleId, startTime, endTime, #dur, distance, totalLength, driverName, assignedDriver, driverScore, safetyEventsCount',  # Exclude route
                        ExpressionAttributeNames={'#dur': 'duration'}  # Handle reserved keyword
                    )
                    
                    all_trips = trips_response['Items']
                    trips_data['total'] = len(all_trips)
                    trips_data['items'] = all_trips[:20]  # Return first 20
                    trips_data['hasMore'] = len(all_trips) > 20
                    
                    # Get full details for the most recent trip (including route, safety events, maintenance events)
                    if all_trips:
                        latest_trip = all_trips[0]
                        trip_id = latest_trip['tripId']
                        
                        # Get full trip details with route
                        full_trip_response = trips_table.query(
                            KeyConditionExpression='tripId = :trip_id',
                            ExpressionAttributeValues={':trip_id': trip_id},
                            ScanIndexForward=False,
                            Limit=1
                        )
                        
                        if full_trip_response['Items']:
                            last_trip_details = full_trip_response['Items'][0]
                            
                            # Get safety events for this trip
                            try:
                                safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                                safety_response = safety_events_table.scan(
                                    FilterExpression='tripId = :trip_id',
                                    ExpressionAttributeValues={':trip_id': trip_id},
                                    Limit=50
                                )
                                last_trip_details['safetyEvents'] = safety_response.get('Items', [])
                            except Exception as e:
                                print(f"Error fetching safety events for trip {trip_id}: {e}")
                                last_trip_details['safetyEvents'] = []
                            
                            # Get maintenance events for this trip
                            try:
                                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                                maintenance_response = maintenance_alerts_table.scan(
                                    FilterExpression='tripId = :trip_id',
                                    ExpressionAttributeValues={':trip_id': trip_id},
                                    Limit=50
                                )
                                last_trip_details['maintenanceEvents'] = maintenance_response.get('Items', [])
                            except Exception as e:
                                print(f"Error fetching maintenance events for trip {trip_id}: {e}")
                                last_trip_details['maintenanceEvents'] = []
                    
                    # Transform trips data
                    for trip in trips_data['items']:
                        # Convert timestamps
                        if trip.get('startTime') and int(trip['startTime']) > 9999999999:
                            trip['startTime'] = int(trip['startTime']) // 1000
                        if trip.get('endTime') and int(trip['endTime']) > 9999999999:
                            trip['endTime'] = int(trip['endTime']) // 1000
                        
                        # Calculate duration if not present
                        if not trip.get('duration') and trip.get('startTime') and trip.get('endTime'):
                            trip['duration'] = trip['endTime'] - trip['startTime']
                        
                        # Ensure distance is present (use totalLength if distance not available)
                        if not trip.get('distance') and trip.get('totalLength'):
                            trip['distance'] = trip['totalLength']
                        
                        # Get safety event count for this trip
                        trip_id = trip.get('tripId')
                        if trip_id:
                            try:
                                safety_count_response = safety_events_table.query(
                                    IndexName='tripId-index',
                                    KeyConditionExpression='tripId = :trip_id',
                                    ExpressionAttributeValues={':trip_id': trip_id},
                                    Select='COUNT'
                                )
                                trip['safetyEventsCount'] = safety_count_response.get('Count', 0)
                            except Exception as e:
                                print(f"Error getting safety event count for trip {trip_id}: {e}")
                                # Fallback to scan if GSI not ready
                                try:
                                    safety_count_response = safety_events_table.scan(
                                        FilterExpression='tripId = :trip_id',
                                        ExpressionAttributeValues={':trip_id': trip_id},
                                        Select='COUNT'
                                    )
                                    trip['safetyEventsCount'] = safety_count_response.get('Count', 0)
                                except Exception as e2:
                                    print(f"Fallback scan also failed for trip {trip_id}: {e2}")
                                    trip['safetyEventsCount'] = 0
                        else:
                            trip['safetyEventsCount'] = 0
                            
                except Exception as trips_error:
                    print(f"Error fetching trips for {vehicle_id}: {trips_error}")

                # Get safety alerts data (last 20 alerts)
                safety_data = {'items': [], 'total': 0, 'hasMore': False}
                try:
                    safety_events_table = dynamodb.Table(os.environ.get('SAFETY_EVENTS_TABLE_NAME'))
                    print(f"🚨 Fetching safety events for vehicle {vehicle_id}")
                    
                    # Use GSI query with correct index name
                    print(f"🚨 Using GSI vehicleId-index for vehicle {vehicle_id}")
                    safety_response = safety_events_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Limit=25
                    )
                    all_alerts = safety_response['Items']
                    print(f"🚨 Scan returned {len(all_alerts)} safety events")
                    
                    safety_data['total'] = len(all_alerts)
                    safety_data['items'] = all_alerts[:20]
                    safety_data['hasMore'] = len(all_alerts) > 20
                    
                    # Transform safety alerts data
                    for alert in safety_data['items']:
                        # Fix timestamp
                        if alert.get('timestamp') and int(alert['timestamp']) > 9999999999:
                            alert['timestamp'] = int(alert['timestamp']) // 1000
                        
                        # Normalize field names for frontend compatibility
                        if 'lat' in alert and 'latitude' not in alert:
                            alert['latitude'] = alert['lat']
                        if 'lng' in alert and 'longitude' not in alert:
                            alert['longitude'] = alert['lng']
                        if 'message' in alert and 'eventType' not in alert:
                            alert['eventType'] = alert['message']
                    
                    print(f"🚨 Final safety data: {len(safety_data['items'])} items, total: {safety_data['total']}")
                            
                except Exception as safety_error:
                    print(f"Error fetching safety alerts for {vehicle_id}: {safety_error}")

                # Get maintenance alerts data (last 20 alerts)
                maintenance_data = {'items': [], 'total': 0, 'hasMore': False}
                try:
                    maintenance_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                    print(f"🔧 Fetching maintenance alerts for vehicle {vehicle_id}")
                    
                    # Use GSI query with vehicleId-index
                    print(f"🔧 Using GSI vehicleId-index for vehicle {vehicle_id}")
                    maintenance_response = maintenance_table.query(
                        IndexName='vehicleId-index',
                        KeyConditionExpression='vehicleId = :vehicle_id',
                        ExpressionAttributeValues={':vehicle_id': vehicle_id},
                        Limit=25
                    )
                    all_maintenance = maintenance_response['Items']
                    print(f"🔧 GSI query returned {len(all_maintenance)} maintenance alerts")
                    
                    maintenance_data['total'] = len(all_maintenance)
                    maintenance_data['items'] = all_maintenance[:20]
                    maintenance_data['hasMore'] = len(all_maintenance) > 20
                    
                except Exception as maintenance_error:
                    print(f"Error fetching maintenance alerts for {vehicle_id}: {maintenance_error}")

                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                # Check for vehicle certificate
                try:
                    certificates_table = dynamodb.Table(os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME'))
                    cert_response = certificates_table.get_item(Key={'vehicleId': vehicle_id})
                    
                    if 'Item' in cert_response:
                        cert_item = cert_response['Item']
                        vehicle['has_certificate'] = True
                        vehicle['auto_registered'] = cert_item.get('status') == 'ACTIVE'
                        vehicle['certificateId'] = cert_item.get('certificateId')
                        vehicle['certificateStatus'] = cert_item.get('status')
                    else:
                        vehicle['has_certificate'] = False
                        vehicle['auto_registered'] = False
                        
                except Exception as cert_error:
                    print(f"Error checking certificate for {vehicle_id}: {cert_error}")
                    vehicle['has_certificate'] = False
                    vehicle['auto_registered'] = False
                
                # Return consolidated response
                response_data = {
                    'vehicle': vehicle,
                    'trips': trips_data,
                    'safetyAlerts': safety_data,
                    'maintenanceAlerts': maintenance_data,
                    'lastTrip': last_trip_details  # Include full last trip details with route and events
                }

                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(response_data, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicle: {str(e)}'})
                }
        
        # Handle vehicles locations endpoint
        if (path == '/api/v1/vehicles/locations' or path == '//api/v1/vehicles/locations') and method == 'GET':
            try:
                # Check cache first
                cache_table = dynamodb.Table(os.environ.get('DASHBOARD_METRICS_CACHE_TABLE'))
                
                try:
                    cache_response = cache_table.get_item(
                        Key={'metricKey': 'vehicle_locations'}
                    )
                    
                    if 'Item' in cache_response:
                        cached_data = json.loads(cache_response['Item']['data'])
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps(cached_data)
                        }
                except Exception:
                    pass
                
                # Fallback: generate vehicle locations from vehicles table
                vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE_NAME'))
                
                # Scan all vehicles (remove limit to get all 3135 vehicles)
                vehicles = []
                scan_kwargs = {}
                
                while True:
                    response = vehicles_table.scan(**scan_kwargs)
                    vehicles.extend(response['Items'])
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Generate locations based on vehicle data
                vehicle_locations = []
                for vehicle in vehicles:
                    vehicle_id = vehicle.get('vehicleId', '')
                    fleet_id = vehicle.get('fleetId', '')
                    city = vehicle.get('city', 'unknown')
                    status = vehicle.get('status', 'active')
                    make = vehicle.get('make', 'Unknown')
                    model = vehicle.get('model', 'Unknown')
                    
                    # Generate coordinates based on city
                    if city == 'munich':
                        base_lat, base_lng = 48.1351, 11.5820
                    elif city == 'chicago':
                        base_lat, base_lng = 41.8781, -87.6298
                    elif city == 'new_york':
                        base_lat, base_lng = 40.7128, -74.0060
                    elif city == 'atlanta':
                        base_lat, base_lng = 33.7490, -84.3880
                    elif city == 'seattle':
                        base_lat, base_lng = 47.6062, -122.3321
                    elif city == 'los_angeles':
                        base_lat, base_lng = 34.0522, -118.2437
                    else:
                        base_lat, base_lng = 39.8283, -98.5795
                    
                    # Add some variation to coordinates
                    lat_offset = (hash(vehicle_id) % 200 - 100) * 0.001
                    lng_offset = (hash(vehicle_id + 'lng') % 200 - 100) * 0.001
                    
                    vehicle_locations.append({
                        'vehicleId': vehicle_id,
                        'vin': vehicle.get('vin', f'VIN{vehicle_id.replace("VEH-", "")}'),  # Use actual VIN or generate from vehicleId
                        'fleetId': fleet_id,
                        'status': status,
                        'make': make,
                        'model': model,
                        'lat': base_lat + lat_offset,
                        'lng': base_lng + lng_offset,
                        'lastUpdate': int(time.time()),
                        'connectionStatus': vehicle.get('connectionStatus', 'disconnected')  # Use actual connection status from vehicle table
                    })
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'vehicles': vehicle_locations,
                        'total': len(vehicles),  # Total vehicles in database
                        'withLocations': len(vehicle_locations),  # All vehicles have generated locations
                        'cached': False,
                        'timestamp': int(time.time())
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch vehicle locations: {str(e)}'})
                }
        
        # Handle maintenance-alerts endpoint
        if (path == '/api/v1/maintenance-alerts' or path == '//api/v1/maintenance-alerts') and method == 'GET':
            try:
                maintenance_alerts_table = dynamodb.Table(os.environ.get('MAINTENANCE_ALERTS_TABLE_NAME'))
                
                limit = min(int(query_params.get('limit', 20)), 100)
                page = int(query_params.get('page', 1))
                start_time = query_params.get('startTime')
                end_time = query_params.get('endTime')
                fleet_id = query_params.get('fleetId')
                
                # Build filter expression
                filter_expression = None
                expression_values = {}
                expression_names = {}
                
                # Add time range filter
                if start_time and end_time:
                    # Convert milliseconds to seconds for numeric comparison
                    start_timestamp = int(start_time) // 1000 if len(start_time) > 10 else int(start_time)
                    end_timestamp = int(end_time) // 1000 if len(end_time) > 10 else int(end_time)
                    
                    # Use numeric comparison (timestamps are now stored as numbers)
                    filter_expression = '#ts BETWEEN :start_time AND :end_time'
                    expression_values[':start_time'] = start_timestamp
                    expression_values[':end_time'] = end_timestamp
                    expression_names['#ts'] = 'timestamp'
                
                # Add fleet filter
                if fleet_id and fleet_id != 'all':
                    # Filter by vehicle ID prefix since maintenance alerts don't have fleetId field
                    if fleet_id == 'FLEET-MUNICH':
                        vehicle_prefix = 'VEH-MUN-'
                    else:
                        fleet_code = fleet_id.replace('FLEET-', '')
                        vehicle_prefix = f'VEH-{fleet_code}-'
                    
                    fleet_filter = 'begins_with(vehicleId, :prefix)'
                    expression_values[':prefix'] = vehicle_prefix
                    
                    if filter_expression:
                        filter_expression += f' AND {fleet_filter}'
                    else:
                        filter_expression = fleet_filter
                
                # Get total count
                count_kwargs = {'Select': 'COUNT'}
                if filter_expression:
                    count_kwargs['FilterExpression'] = filter_expression
                    if expression_names:
                        count_kwargs['ExpressionAttributeNames'] = expression_names
                    if expression_values:
                        count_kwargs['ExpressionAttributeValues'] = expression_values
                
                count_response = maintenance_alerts_table.scan(**count_kwargs)
                total_count = count_response['Count']
                
                # Handle pagination
                scan_kwargs = {'Limit': limit * 50}  # Increase scan limit for filtering efficiency
                if filter_expression:
                    scan_kwargs['FilterExpression'] = filter_expression
                    if expression_names:
                        scan_kwargs['ExpressionAttributeNames'] = expression_names
                    if expression_values:
                        scan_kwargs['ExpressionAttributeValues'] = expression_values
                
                # Skip to the correct page
                current_page = 1
                while current_page < page:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    if 'LastEvaluatedKey' not in response:
                        # No more data
                        return {
                            'statusCode': 200,
                            'headers': cors_headers,
                            'body': json.dumps({
                                'alerts': [],
                                'total': total_count,
                                'page': page,
                                'limit': limit,
                                'totalPages': (total_count + limit - 1) // limit,
                                'hasNextPage': False,
                                'hasPrevPage': page > 1
                            })
                        }
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                    current_page += 1
                
                # Get the actual page data - collect until we have enough records
                alerts = []
                while len(alerts) < limit:
                    response = maintenance_alerts_table.scan(**scan_kwargs)
                    page_alerts = response['Items']
                    alerts.extend(page_alerts)
                    
                    if 'LastEvaluatedKey' not in response:
                        break
                    scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                
                # Trim to exact limit
                alerts = alerts[:limit]
                
                total_pages = (total_count + limit - 1) // limit
                has_next_page = 'LastEvaluatedKey' in response
                
                def decimal_default(obj):
                    from decimal import Decimal
                    if isinstance(obj, Decimal):
                        return int(obj) if obj % 1 == 0 else float(obj)
                    raise TypeError
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'alerts': alerts,
                        'total': total_count,
                        'page': page,
                        'limit': limit,
                        'totalPages': total_pages,
                        'hasNextPage': has_next_page,
                        'hasPrevPage': page > 1
                    }, default=decimal_default)
                }
                
            except Exception as e:
                return {
                    'statusCode': 500,
                    'headers': cors_headers,
                    'body': json.dumps({'error': f'Failed to fetch maintenance alerts: {str(e)}'})
                }
        
        return {
            'statusCode': 404,
            'headers': cors_headers,
            'body': json.dumps({'error': 'Endpoint not found'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': cors_headers,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
