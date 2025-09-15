/**
 *  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 *
 *  Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
 *  with the License. A copy of the License is located at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 *  or in the 'license' file accompanying this file. This file is distributed on an 'AS IS' BASIS, WITHOUT WARRANTIES
 *  OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions
 *  and limitations under the License.
 */

import { join } from 'path';
import {
  Aws,
  CfnOutput,
  RemovalPolicy,
  SymlinkFollowMode,
  Duration,
  aws_ec2 as ec2,
  aws_rds as rds,
  aws_iam as iam,
  aws_sns as sns,
  aws_sqs as sqs,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
} from 'aws-cdk-lib';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { SqsSubscription } from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { config } from './config';

export interface AlarmProps {
  vpc: ec2.IVpc;
  securityGroup: ec2.SecurityGroup;
  rdsProxy: rds.DatabaseProxy;
  credentialSecret: secretsmanager.Secret;
}

export class Alarm extends Construct {
  readonly alarmRecorder: lambda.Function;

  constructor(scope: Construct, id: string, props: AlarmProps) {
    super(scope, id);

    const alarmSnsTopic = new sns.Topic(this, 'AlarmSnsTopic', {});
    (alarmSnsTopic.node.defaultChild as sns.CfnTopic).overrideLogicalId('AlarmSnsTopic');

    const alarmDLQ = new sqs.Queue(this, 'AlarmDLQ', {
      visibilityTimeout: Duration.minutes(15),
      retentionPeriod: Duration.days(1),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Override the logical ID
    (alarmDLQ.node.defaultChild as sqs.CfnQueue).overrideLogicalId('AlarmDLQ');

    const alarmDLQPolicy = new sqs.CfnQueuePolicy(
      this,
      'AlarmDLQPolicy',
      {
        queues: [alarmDLQ.queueName],
        policyDocument: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['SQS:*'],
              effect: iam.Effect.DENY,
              resources: [alarmDLQ.queueArn],
              conditions: {
                ['Bool']: {
                  'aws:SecureTransport': 'false',
                },
              },
              principals: [new iam.AnyPrincipal()],
            }),
          ],
        }),
      },
    );

    alarmDLQPolicy.overrideLogicalId('AlarmDLQPolicy');

    const alarmQ = new sqs.Queue(this, 'AlarmQ', {
      visibilityTimeout: Duration.minutes(15),
      retentionPeriod: Duration.days(1),
      deadLetterQueue: {
        queue: alarmDLQ,
        maxReceiveCount: 3,
      },
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Override the logical ID
    (alarmQ.node.defaultChild as sqs.CfnQueue).overrideLogicalId('AlarmQ');

    const alarmQPolicy = new sqs.CfnQueuePolicy(
      this,
      'AlarmQPolicy',
      {
        queues: [alarmQ.queueName],
        policyDocument: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['SQS:*'],
              effect: iam.Effect.DENY,
              resources: [alarmQ.queueArn],
              conditions: {
                ['Bool']: {
                  'aws:SecureTransport': 'false',
                },
              },
              principals: [new iam.AnyPrincipal()],
            }),
            new iam.PolicyStatement({
              actions: ['SQS:SendMessage'],
              effect: iam.Effect.ALLOW,
              resources: [alarmQ.queueArn],
              principals: [new iam.AnyPrincipal()],
              conditions: {
                ['ArnEquals']: {
                  'aws:SourceArn': alarmSnsTopic.topicArn,
                },
              },
            }),
          ],
        }),
      },
    );

    alarmQPolicy.overrideLogicalId('AlarmQPolicy');

    alarmSnsTopic.addSubscription(new SqsSubscription(alarmQ, {
      rawMessageDelivery: true,
    }));

    const alarmLayer = new lambda.LayerVersion(
      this,
      'AlarmsLayer',
      {
        removalPolicy: RemovalPolicy.DESTROY,
        code: lambda.AssetCode.fromAsset(join(__dirname, '../lambda/'), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            platform: 'linux/amd64',
            command: [
              '/bin/bash',
              '-c',
              'find -L ./alarm_recorder -name "requirements.txt" -exec pip install -r {} -t /asset-output/python \\;',
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      },
    );

    // Override the logical ID
    (alarmLayer.node.defaultChild as lambda.CfnLayerVersion).overrideLogicalId('AlarmLayer');

    this.alarmRecorder = new lambda.Function(this, 'AlarmRecorder', {
      code: lambda.AssetCode.fromAsset(
        join(__dirname, '../lambda/alarm_recorder/'),
        { followSymlinks: SymlinkFollowMode.ALWAYS },
      ),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      timeout: Duration.minutes(15),
      memorySize: 256,
      layers: [alarmLayer],
      environment: {
        RDS_PROXY_ENDPOINT: props.rdsProxy.endpoint,
        CREDENTIAL_SECRET_NAME: props.credentialSecret.secretName,
        DATABASE_NAME: config.databaseName,
      },
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [props.securityGroup],
      description: `${Aws.STACK_NAME} - Lambda function to write alarm events to database.`,
    });

    (this.alarmRecorder.node.defaultChild as lambda.CfnFunction).overrideLogicalId('AlarmRecorder');

    this.alarmRecorder.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'sqs:ReceiveMessage',
        'sqs:DeleteMessage',
        'sqs:GetQueueAttributes',
        'sqs:ChangeMessageVisibility',
        'sqs:GetQueueUrl',
      ],
      resources: [
        `${alarmQ.queueArn}`,
        `${alarmDLQ.queueArn}`,
      ],
    }));
    props.rdsProxy.grantConnect(this.alarmRecorder);
    props.credentialSecret.grantRead(this.alarmRecorder);

    const alarmRecorderSource = new SqsEventSource(
      alarmQ,
      {
        batchSize: 10,
      },
    );
    this.alarmRecorder.addEventSource(alarmRecorderSource);

    new CfnOutput(this, 'AlarmSnsTopicName', {
      description: 'Alarm SNS Topic Name',
      value: alarmSnsTopic.topicName,
    }).overrideLogicalId('AlarmSnsTopicName');

    new CfnOutput(this, 'AlarmQueueName', {
      description: 'Alarm Queue Name',
      value: alarmQ.queueName,
    }).overrideLogicalId('AlarmQueueName');

  }
}