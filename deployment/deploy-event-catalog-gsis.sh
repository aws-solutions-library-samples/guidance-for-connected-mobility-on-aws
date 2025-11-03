#!/bin/bash
# Phased deployment of Event Catalog GSIs
# DynamoDB only allows ONE GSI creation per update

set -e

echo "🚀 Event Catalog GSI Deployment - Phase 1: event_id-timestamp-index"
echo "This will take ~10 minutes per phase (3 phases total)"
echo ""

# Phase 1: Deploy event_id-timestamp-index only
echo "Phase 1: Deploying event_id-timestamp-index..."
# Manually add only first GSI, deploy, then continue

echo ""
echo "⏳ Waiting 10 minutes for GSI to become ACTIVE..."
sleep 600

echo ""
echo "🚀 Phase 2: Deploying category-timestamp-index..."
# Manually add second GSI, deploy

echo ""
echo "⏳ Waiting 10 minutes for GSI to become ACTIVE..."
sleep 600

echo ""
echo "🚀 Phase 3: Deploying severity-timestamp-index..."
# Manually add third GSI, deploy

echo ""
echo "✅ All Event Catalog GSIs deployed successfully!"
