import json
import boto3
import os
from datetime import datetime
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()
processor = BatchProcessor(event_type=EventType.SQS)

# DynamoDB client
dynamodb = boto3.resource('dynamodb')
alarms_table = dynamodb.Table(os.environ['ALARMS_TABLE'])

@tracer.capture_method
def record_handler(record: SQSRecord):
    """Process CloudWatch alarm event"""
    try:
        payload = json.loads(record.body)
        
        # Extract alarm data from CloudWatch event
        alarm_data = payload.get('detail', {})
        
        item = {
            'alarm_name': alarm_data.get('alarmName', 'unknown'),
            'alarm_arn': alarm_data.get('alarmArn', ''),
            'alarm_description': alarm_data.get('alarmDescription', ''),
            'aws_account_id': payload.get('account', ''),
            'region': payload.get('region', ''),
            'new_state_value': alarm_data.get('state', {}).get('value', 'UNKNOWN'),
            'new_state_reason': alarm_data.get('state', {}).get('reason', ''),
            'state_change_timestamp': int(datetime.utcnow().timestamp() * 1000),
            'old_state_value': alarm_data.get('previousState', {}).get('value', 'UNKNOWN'),
            'trigger': alarm_data.get('configuration', {}),
            'message': payload,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        alarms_table.put_item(Item=item)
        logger.info(f"Recorded alarm: {item['alarm_name']} - {item['new_state_value']}")
        
    except Exception as e:
        logger.error(f"Error processing alarm record: {str(e)}")
        raise

@logger.inject_lambda_context
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Main Lambda handler"""
    return process_partial_response(
        event=event,
        record_handler=record_handler,
        processor=processor,
        context=context
    )
