#!/bin/bash

AWS_PROFILE=${AWS_PROFILE:-default}
AWS_REGION=${AWS_REGION:-us-east-1}
DEPLOYMENT_STAGE=${DEPLOYMENT_STAGE:-dev}

echo "🔧 Updating all Flink applications with new JAR..."

# Get all CMS Flink applications
APPS=$(aws kinesisanalyticsv2 list-applications --region $AWS_REGION --profile $AWS_PROFILE --query "ApplicationSummaries[?contains(ApplicationName, 'cms-$DEPLOYMENT_STAGE-flink')].ApplicationName" --output text)

# First, stop all running applications
echo "🛑 Stopping all running applications..."
for app in $APPS; do
    if [ -n "$app" ]; then
        STATUS=$(aws kinesisanalyticsv2 describe-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE --query 'ApplicationDetail.ApplicationStatus' --output text)
        if [ "$STATUS" = "RUNNING" ]; then
            echo "  Stopping $app..."
            aws kinesisanalyticsv2 stop-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE 2>/dev/null
        fi
    fi
done

# Wait for all applications to stop
echo "⏳ Waiting for applications to stop..."
for app in $APPS; do
    if [ -n "$app" ]; then
        while true; do
            STATUS=$(aws kinesisanalyticsv2 describe-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE --query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null)
            if [ "$STATUS" = "READY" ]; then
                echo "  ✅ $app is READY"
                break
            elif [ "$STATUS" = "STOPPING" ]; then
                echo "  ⏳ $app is stopping..."
                sleep 5
            else
                echo "  📊 $app status: $STATUS"
                sleep 3
            fi
        done
    fi
done

echo "🚀 Now running Makefile configure-flink..."
make configure-flink AWS_PROFILE=$AWS_PROFILE DEPLOYMENT_STAGE=$DEPLOYMENT_STAGE AWS_REGION=$AWS_REGION
