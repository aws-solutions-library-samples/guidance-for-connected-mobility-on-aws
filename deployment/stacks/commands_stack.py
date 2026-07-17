"""
Commands Stack - Remote vehicle commands via IoT Core MQTT.

Architecture:
  UI -> API Gateway -> commands_lambda -> IoT Core publish -> vehicle
  Vehicle -> IoT Core response topic -> IoT Rule -> command_response_handler -> DDB update
"""
import os
from aws_cdk import (
    Fn, Stack, Duration, RemovalPolicy, CfnOutput, BundlingOptions,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_iot as iot,
    aws_logs as logs,
)
from constructs import Construct


class CommandsStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        prefix = f'cms-{stage}'

        # ── Commands API Lambda ──────────────────────────────────────
        commands_code_path = os.path.join(os.path.dirname(__file__), '../../services/commands')
        
        # Pre-install dependencies locally to avoid Docker bundling
        commands_asset_path = os.path.join(os.path.dirname(__file__), '../../services/commands/.build')
        os.makedirs(commands_asset_path, exist_ok=True)
        import subprocess, sys
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(commands_code_path, 'requirements.txt'),
                       '-t', commands_asset_path, '-q', '--upgrade'], check=True)
        for f in os.listdir(commands_code_path):
            if f.endswith('.py'):
                import shutil
                shutil.copy2(os.path.join(commands_code_path, f), commands_asset_path)
        
        commands_lambda = lambda_.Function(self, 'CommandsApi',
            function_name=f'{prefix}-commands-api',
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler='commands_lambda.handler',
            code=lambda_.Code.from_asset(commands_asset_path),
            timeout=Duration.seconds(15),
            memory_size=256,
            environment={
                'DEPLOYMENT_STAGE': stage,
                'COMMANDS_TABLE': f'{prefix}-storage-commands',
                'SIGNAL_CATALOG_TABLE': f'{prefix}-signal-catalog',
                'GEOFENCES_TABLE': f'{prefix}-storage-geofences',
            })

        # IoT publish + DDB access
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['iot:Publish'], resources=['*']))
        commands_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem', 'dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:Scan'],
            resources=[
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands/index/*',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-signal-catalog',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-geofences',
                f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-geofences/index/*',
            ]))

        # ── Response Handler Lambda ──────────────────────────────────
        response_handler = lambda_.Function(self, 'CommandResponseHandler',
            function_name=f'{prefix}-command-response-handler',
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler='command_response_handler.handler',
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '../../services/commands')),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                'DEPLOYMENT_STAGE': stage,
                'COMMANDS_TABLE': f'{prefix}-storage-commands',
            })

        response_handler.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:UpdateItem', 'dynamodb:GetItem'],
            resources=[f'arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-storage-commands']))

        # ── IoT Rule - route FWE protobuf command responses to Lambda ──
        fwe_response_rule = iot.CfnTopicRule(self, 'FweCommandResponseRule',
            rule_name=f'{prefix.replace("-", "_")}_fwe_command_response_rule',
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                aws_iot_sql_version='2016-03-23',
                sql="SELECT encode(*, 'base64') AS b64_payload, topic(3) AS vehicleId FROM 'cms/commands/things/+/executions/+/response/protobuf'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=response_handler.function_arn,
                        )
                    )
                ],
                rule_disabled=False,
            ))

        # ── IoT Rule - route legacy JSON command responses to Lambda ──
        response_rule = iot.CfnTopicRule(self, 'CommandResponseRule',
            rule_name=f'{prefix.replace("-", "_")}_command_response_rule',
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/commands/+/response'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=response_handler.function_arn,
                        )
                    )
                ],
                rule_disabled=False,
            ))

        # Allow IoT to invoke the response handler
        response_handler.add_permission('IoTInvoke',
            principal=iam.ServicePrincipal('iot.amazonaws.com'),
            source_arn=f'arn:aws:iot:{self.region}:{self.account}:rule/{prefix.replace("-", "_")}_command_response_rule')
        response_handler.add_permission('IoTInvokeFwe',
            principal=iam.ServicePrincipal('iot.amazonaws.com'),
            source_arn=f'arn:aws:iot:{self.region}:{self.account}:rule/{prefix.replace("-", "_")}_fwe_command_response_rule')

        # ── API Gateway ──────────────────────────────────────────────
        api = apigateway.RestApi(self, 'CommandsAPI',
            rest_api_name=f'{prefix}-commands-api',
            description='CMS Remote Commands API',
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'Authorization'],
            ))

        # Cognito authorizer — imports User Pool ARN from ui_stack via cross-stack ref
        ui_construct_id = f'cms-{stage}-ui'
        user_pool_arn = Fn.import_value(f'{ui_construct_id}-user-pool-arn')
        user_pool = cognito.UserPool.from_user_pool_arn(self, 'ImportedUserPool', user_pool_arn)
        cognito_authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self, 'CommandsCognitoAuthorizer',
            cognito_user_pools=[user_pool],
            authorizer_name=f'{construct_id}-cognito-auth'
        )
        auth_kwargs = dict(authorizer=cognito_authorizer,
                           authorization_type=apigateway.AuthorizationType.COGNITO)

        integration = apigateway.LambdaIntegration(commands_lambda)

        # /api/commands/catalog
        api_res = api.root.add_resource('api')
        cmd_res = api_res.add_resource('commands')
        catalog = cmd_res.add_resource('catalog')
        catalog.add_method('GET', integration, **auth_kwargs)

        # /api/commands/{vehicleId}
        vehicle_cmd = cmd_res.add_resource('{vehicleId}')
        vehicle_cmd.add_method('POST', integration, **auth_kwargs)  # send command
        vehicle_cmd.add_method('GET', integration, **auth_kwargs)    # get history

        # /api/geofences
        gf_res = api_res.add_resource('geofences')
        gf_res.add_method('POST', integration, **auth_kwargs)  # create geofence
        gf_vehicle = gf_res.add_resource('{vehicleId}')
        gf_vehicle.add_method('GET', integration, **auth_kwargs)     # list geofences for vehicle
        gf_vehicle.add_method('DELETE', integration, **auth_kwargs)   # delete geofence

        # ── Outputs ──────────────────────────────────────────────────
        CfnOutput(self, 'CommandsApiUrl', value=api.url,
                  description='Commands API endpoint - add to runtimeConfig as commandsApiEndpoint')
        CfnOutput(self, 'CommandResponseRuleName',
                  value=f'{prefix.replace("-", "_")}_command_response_rule')
