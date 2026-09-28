#!/bin/bash

AWS_PROFILE=${AWS_PROFILE:-default}
AWS_REGION=${AWS_REGION:-us-east-1}
DEPLOYMENT_STAGE=${DEPLOYMENT_STAGE:-dev}

echo "🔧 Checking Flink application status..."

# Get all CMS Flink applications
APPS=$(aws kinesisanalyticsv2 list-applications --region $AWS_REGION --profile $AWS_PROFILE --query "ApplicationSummaries[?contains(ApplicationName, 'cms-$DEPLOYMENT_STAGE-flink')].ApplicationName" --output text)

for app in $APPS; do
    if [ -n "$app" ]; then
        echo "Checking $app..."
        STATUS=$(aws kinesisanalyticsv2 describe-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE --query 'ApplicationDetail.ApplicationStatus' --output text)
        echo "  Status: $STATUS"
        
        if [ "$STATUS" = "UPDATING" ]; then
            echo "  ⏳ Application is stuck in UPDATING status. Attempting to stop..."
            aws kinesisanalyticsv2 stop-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE --force 2>/dev/null
            echo "  🛑 Stop command sent. Waiting for application to stop..."
            
            # Wait for application to stop
            while true; do
                NEW_STATUS=$(aws kinesisanalyticsv2 describe-application --application-name "$app" --region $AWS_REGION --profile $AWS_PROFILE --query 'ApplicationDetail.ApplicationStatus' --output text 2>/dev/null)
                if [ "$NEW_STATUS" = "READY" ]; then
                    echo "  ✅ $app is now READY"
                    break
                elif [ "$NEW_STATUS" = "STOPPING" ]; then
                    echo "  ⏳ $app is stopping..."
                    sleep 10
                else
                    echo "  📊 $app status: $NEW_STATUS"
                    sleep 5
                fi
            done
        elif [ "$STATUS" = "READY" ]; then
            echo "  ✅ $app is READY"
        else
            echo "  📊 $app status: $STATUS"
        fi
    fi
done

echo "🎯 All applications checked. You can now run the Makefile configure-flink target."
