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
import { SqsQueueAction } from '@aws-cdk/aws-iot-actions-alpha';
import { IotSql, TopicRule } from '@aws-cdk/aws-iot-alpha';
import {
  Aws,
  RemovalPolicy,
  SymlinkFollowMode,
  Duration,
  aws_ec2 as ec2,
  aws_rds as rds,
  aws_iam as iam,
  aws_sqs as sqs,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
} from 'aws-cdk-lib';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import { config } from './config';


export interface IotLifecycleEventsProps {
  vpc: ec2.IVpc;
  securityGroup: ec2.SecurityGroup;
  rdsProxy: rds.DatabaseProxy;
  credentialSecret: secretsmanager.Secret;
}

export class IotLifecycleEvents extends Construct {
  readonly securityGroup: ec2.SecurityGroup;
  readonly lifecycleEvents: lambda.Function;

  constructor(scope: Construct, id: string, props: IotLifecycleEventsProps) {
    super(scope, id);

    const lifecycleEventsDLQ = new sqs.Queue(this, 'LifecycleEventsDLQ', {
      visibilityTimeout: Duration.minutes(15),
      retentionPeriod: Duration.days(1),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Override the logical ID
    (lifecycleEventsDLQ.node.defaultChild as sqs.CfnQueue).overrideLogicalId('LifecycleEventsDLQ');

    const lifecycleEventsDLQPolicy = new sqs.CfnQueuePolicy(
      this,
      'LifecycleEventDLQPolicy',
      {
        queues: [lifecycleEventsDLQ.queueName],
        policyDocument: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['SQS:*'],
              effect: iam.Effect.DENY,
              resources: [lifecycleEventsDLQ.queueArn],
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

    lifecycleEventsDLQPolicy.overrideLogicalId('LifecycleEventsDLQPolicy');

    const lifecycleEventsQ = new sqs.Queue(this, 'LifecycleEventsQ', {
      visibilityTimeout: Duration.minutes(15),
      retentionPeriod: Duration.days(1),
      deadLetterQueue: {
        queue: lifecycleEventsDLQ,
        maxReceiveCount: 3,
      },
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Override the logical ID
    (lifecycleEventsQ.node.defaultChild as sqs.CfnQueue).overrideLogicalId('LifecycleEventsQ');

    const lifecycleEventsQPolicy = new sqs.CfnQueuePolicy(
      this,
      'LifecycleEventsQPolicy',
      {
        queues: [lifecycleEventsQ.queueName],
        policyDocument: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['SQS:*'],
              effect: iam.Effect.DENY,
              resources: [lifecycleEventsQ.queueArn],
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

    lifecycleEventsQPolicy.overrideLogicalId('LifecycleEventsQPolicy');

    const lifecycleEventsDelayQ = new sqs.Queue(this, 'LifecycleEventsDelayQ', {
      deliveryDelay: Duration.seconds(10),
      visibilityTimeout: Duration.minutes(15),
      retentionPeriod: Duration.days(1),
      deadLetterQueue: {
        queue: lifecycleEventsDLQ,
        maxReceiveCount: 3,
      },
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // Override the logical ID
    (lifecycleEventsDelayQ.node.defaultChild as sqs.CfnQueue).overrideLogicalId('LifecycleEventsDelayQ');

    const lifecycleEventsDelayQPolicy = new sqs.CfnQueuePolicy(
      this,
      'LifecycleEventsDelayQPolicy',
      {
        queues: [lifecycleEventsDelayQ.queueName],
        policyDocument: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['SQS:*'],
              effect: iam.Effect.DENY,
              resources: [lifecycleEventsDelayQ.queueArn],
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

    lifecycleEventsDelayQPolicy.overrideLogicalId('LifecycleEventsDelayQPolicy');

    const lifecycleEventsLayer = new lambda.LayerVersion(
      this,
      'LifecycleEventsLayer',
      {
        removalPolicy: RemovalPolicy.DESTROY,
        code: lambda.AssetCode.fromAsset(join(__dirname, '../lambda/'), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            platform: 'linux/amd64',
            command: [
              '/bin/bash',
              '-c',
              'find -L ./iot_lifecycle_events -name "requirements.txt" -exec pip install -r {} -t /asset-output/python \\;',
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      },
    );

    // Override the logical ID
    (lifecycleEventsLayer.node.defaultChild as lambda.CfnLayerVersion).overrideLogicalId('LifecycleEventsLayer');

    this.lifecycleEvents = new lambda.Function(this, 'LifecycleEvents', {
      code: lambda.AssetCode.fromAsset(
        join(__dirname, '../lambda/iot_lifecycle_events/'),
        { followSymlinks: SymlinkFollowMode.ALWAYS },
      ),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      timeout: Duration.minutes(15),
      memorySize: 256,
      layers: [lifecycleEventsLayer],
      environment: {
        RDS_PROXY_ENDPOINT: props.rdsProxy.endpoint,
        CREDENTIAL_SECRET_NAME: props.credentialSecret.secretName,
        DATABASE_NAME: config.databaseName,
      },
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [props.securityGroup],
      description: `${Aws.STACK_NAME} - Lambda function to process Lifecycle events of AWS IoT Core.`,
    });

    (this.lifecycleEvents.node.defaultChild as lambda.CfnFunction).overrideLogicalId('LifecycleEvents');

    this.lifecycleEvents.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iot:DescribeThing'],
      resources: [`arn:${Aws.PARTITION}:iot:*:${Aws.ACCOUNT_ID}:thing/*`],
    }));
    this.lifecycleEvents.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'sqs:ReceiveMessage',
        'sqs:DeleteMessage',
        'sqs:GetQueueAttributes',
        'sqs:ChangeMessageVisibility',
        'sqs:GetQueueUrl',
      ],
      resources: [
        `${lifecycleEventsQ.queueArn}`,
        `${lifecycleEventsDelayQ.queueArn}`,
        `${lifecycleEventsDLQ.queueArn}`,
      ],
    }));
    props.rdsProxy.grantConnect(this.lifecycleEvents);
    props.credentialSecret.grantRead(this.lifecycleEvents);

    const lifecycleEventsSource = new SqsEventSource(
      lifecycleEventsQ,
      {
        batchSize: 10,
      },
    );
    const lifecycleEventsDelaySource = new SqsEventSource(
      lifecycleEventsDelayQ,
      {
        batchSize: 10,
      },
    );
    this.lifecycleEvents.addEventSource(lifecycleEventsSource);
    this.lifecycleEvents.addEventSource(lifecycleEventsDelaySource);

    new TopicRule(this, 'ConnectTopicRule', {
      sql: IotSql.fromStringAsVer20160323("SELECT * FROM '$aws/events/presence/connected/#'"),
      actions: [
        new SqsQueueAction(lifecycleEventsQ, {}),
      ],
    });

    new TopicRule(this, 'DisconnectTopicRule', {
      sql: IotSql.fromStringAsVer20160323("SELECT * FROM '$aws/events/presence/disconnected/#'"),
      actions: [
        new SqsQueueAction(lifecycleEventsDelayQ, {}),
      ],
    });

    new TopicRule(this, 'SubscribeTopicRule', {
      sql: IotSql.fromStringAsVer20160323("SELECT * FROM '$aws/events/subscriptions/subscribed/#'"),
      actions: [
        new SqsQueueAction(lifecycleEventsQ, {}),
      ],
    });

    new TopicRule(this, 'UnsubscribeTopicRule', {
      sql: IotSql.fromStringAsVer20160323("SELECT * FROM '$aws/events/subscriptions/unsubscribed/#'"),
      actions: [
        new SqsQueueAction(lifecycleEventsQ, {}),
      ],
    });

  }
}