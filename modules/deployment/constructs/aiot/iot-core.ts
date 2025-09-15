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
  RemovalPolicy,
  SymlinkFollowMode,
  Duration,
  CfnOutput,
  aws_ec2 as ec2,
  aws_rds as rds,
  aws_lambda as lambda,
  aws_secretsmanager as secretsmanager,
  aws_iot as iot,
  aws_iam as iam,
} from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { config } from './config';


export interface IotCoreProps {
  vpc: ec2.IVpc;
  securityGroup: ec2.SecurityGroup;
  rdsProxy: rds.DatabaseProxy;
  credentialSecret: secretsmanager.Secret;
}

export class IotCore extends Construct {
  readonly iotCustomAuthorizerFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: IotCoreProps) {
    super(scope, id);

    const iotCustomAuthorizerName = config.iotCustomAuthorizerName;
    const iotCustomAuthDomainConfName = config.ioTCustomAuthDomainConfigurationName;

    const iotCustomAuthorizerFunctionLayer = new lambda.LayerVersion(
      this,
      'IotCustomAuthorizerFunctionLayer',
      {
        removalPolicy: RemovalPolicy.DESTROY,
        code: lambda.AssetCode.fromAsset(join(__dirname, '../lambda/'), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            platform: 'linux/amd64',
            command: [
              '/bin/bash',
              '-c',
              'find -L ./iot_custom_authorizer -name "requirements.txt" -exec pip install -r {} -t /asset-output/python \\;',
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      },
    );

    // Override the logical ID
    (iotCustomAuthorizerFunctionLayer.node.defaultChild as lambda.CfnLayerVersion).overrideLogicalId('IotCustomAuthorizerFunctionLayer');

    this.iotCustomAuthorizerFunction = new lambda.Function(this, 'IotCustomAuthorizerFunction', {
      code: lambda.AssetCode.fromAsset(
        join(__dirname, '../lambda/iot_custom_authorizer/'),
        { followSymlinks: SymlinkFollowMode.ALWAYS },
      ),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_function.lambda_handler',
      timeout: Duration.minutes(1),
      memorySize: 128,
      layers: [iotCustomAuthorizerFunctionLayer],
      environment: {
        RDS_PROXY_ENDPOINT: props.rdsProxy.endpoint,
        CREDENTIAL_SECRET_NAME: props.credentialSecret.secretName,
        DATABASE_NAME: config.databaseName,
      },
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [props.securityGroup],
      description: `${Aws.STACK_NAME} - A custom authorizer for AWS IoT Core.`,
    });

    (this.iotCustomAuthorizerFunction.node.defaultChild as lambda.CfnFunction).overrideLogicalId('IotCustomAuthorizerFunction');

    props.rdsProxy.grantConnect(this.iotCustomAuthorizerFunction);
    props.credentialSecret.grantRead(this.iotCustomAuthorizerFunction);

    const iotCustomAuthorizer = new iot.CfnAuthorizer(this, 'IotCustomAuthorizer', {
      authorizerName: iotCustomAuthorizerName,
      authorizerFunctionArn: this.iotCustomAuthorizerFunction.functionArn,
      status: 'ACTIVE',
      enableCachingForHttp: false,
      signingDisabled: true,
      // tokenKeyName: 'tokenKeyName',
      // tokenSigningPublicKeys: {
      //   tokenSigningPublicKeysKey: 'tokenSigningPublicKeys',
      // },
    });
    iotCustomAuthorizer.overrideLogicalId('IotCustomAuthorizer');

    const iotCustomAuthDomain = new iot.CfnDomainConfiguration(this, 'IotCustomAuthDomain', {
      domainConfigurationName: iotCustomAuthDomainConfName,
      applicationProtocol: 'DEFAULT',
      authenticationType: 'DEFAULT',
      authorizerConfig: {
        allowAuthorizerOverride: true,
        defaultAuthorizerName: iotCustomAuthorizerName,
      },
      // clientCertificateConfig: {
      //   clientCertificateCallbackArn: 'clientCertificateCallbackArn',
      // },
      // domainConfigurationName: 'domainConfigurationName',
      domainConfigurationStatus: 'ENABLED',
      // domainName: 'IotCustomAuthDomain',
      // serverCertificateArns: ['serverCertificateArns'],
      // serverCertificateConfig: {
      //   enableOcspCheck: false,
      // },
    //   serviceType: 'serviceType',
    //   tlsConfig: {
    //     securityPolicy: 'securityPolicy',
    //   },
    //   validationCertificateArn: 'validationCertificateArn',
    });
    iotCustomAuthDomain.overrideLogicalId('IotCustomAuthDomain');
    iotCustomAuthDomain.node.addDependency(iotCustomAuthorizer);

    this.iotCustomAuthorizerFunction.addPermission('AllowIotCustomAuthorizerInvoke', {
      principal: new iam.ServicePrincipal('iot.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: iotCustomAuthorizer.attrArn,
    });

    new CfnOutput(this, 'IoTCustomAuthorizerName', {
      description: 'The name of IoT Core Authorizer',
      value: iotCustomAuthorizerName,
    }).overrideLogicalId('IoTCustomAuthorizerName');

    new CfnOutput(this, 'IoTCustomAuthDomainConfigurationName', {
      description: 'The name of IoT Core domain configuration',
      value: iotCustomAuthDomainConfName,
    }).overrideLogicalId('IoTCustomAuthDomainConfigurationName');
  }
}