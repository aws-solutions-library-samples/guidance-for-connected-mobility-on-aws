import os
import boto3
from app.metrics import MetricsUtil
from aws_lambda_powertools import Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, CORSConfig

AWS_REGION = os.environ.get("AWS_REGION")

athena_client = boto3.client("athena", region_name=AWS_REGION)
cwl_client = boto3.client("cloudwatch", region_name=AWS_REGION)
iot_client = boto3.client("iot", region_name=AWS_REGION)
iot_data_client = boto3.client("iot-data", region_name=AWS_REGION)
logs_client = boto3.client("logs", region_name=AWS_REGION)

metrics_util = MetricsUtil(cwl_client)
tracer = Tracer()

cors_config = CORSConfig(allow_origin="*")
app = APIGatewayRestResolver(
    cors=cors_config, enable_validation=True, strip_prefixes=["/v1"]
)
