#!/bin/bash
# Build custom FWE image:
#   - No Store-and-Forward (eliminates $aws/things jobs + $aws/events subscriptions)
#   - No command response accepted/rejected subscriptions (eliminates 143 errors)
#   - Keeps: remote commands, last known state, CAN, OBD
#
# Usage: ./build_custom_fwe.sh [--push]
set -euo pipefail

IMAGE_NAME="cms-fwe-edge"
IMAGE_TAG="v1.3.2-custom"
FWE_VERSION="v1.3.2"
PUSH=false

if [ "${1:-}" == "--push" ]; then
    PUSH=true
fi

echo "🔧 Building custom FWE image (no Store-and-Forward, no spurious subscriptions)"
echo "   Base: aws-iot-fleetwise-edge ${FWE_VERSION}"
echo ""

WORK_DIR=$(mktemp -d)
trap "rm -rf ${WORK_DIR}" EXIT
cd "${WORK_DIR}"

echo "📥 Cloning FWE ${FWE_VERSION}..."
git clone --depth 1 --branch "${FWE_VERSION}" https://github.com/aws/aws-iot-fleetwise-edge.git
cd aws-iot-fleetwise-edge

# ── Patch: remove command response accepted/rejected subscriptions ──
# These only work with the $aws/commands/ service prefix and cause 143 errors
# when using a custom commandsTopicPrefix. FWE doesn't need them to function.
echo "🩹 Patching IoTFleetWiseEngine.cpp..."
sed -i '/#ifdef FWE_FEATURE_REMOTE_COMMANDS/,/#endif/ {
    /receiverAcceptedCommandResponse = mConnectivityModule->createReceiver/d
    /receiverRejectedCommandResponse = mConnectivityModule->createReceiver/d
}' src/IoTFleetWiseEngine.cpp

echo "📦 Installing dependencies..."
sudo ./tools/install-deps-native.sh

echo "🔨 Building FWE binary..."
./tools/build-fwe-native.sh \
    --with-remote-commands-support \
    --with-lks-support

ARCH_DIR="linux/$(dpkg --print-architecture 2>/dev/null || echo "arm64")"
mkdir -p "tools/container/${ARCH_DIR}"
tar -czf "tools/container/${ARCH_DIR}/aws-iot-fleetwise-edge.tar.gz" -C build aws-iot-fleetwise-edge
tar -czf "tools/container/${ARCH_DIR}/opt.tar.gz" --files-from /dev/null

echo "🐳 Building Docker image..."
docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f tools/container/Dockerfile \
    --build-arg TARGETPLATFORM="${ARCH_DIR}" \
    .

echo ""
echo "✅ Image built: ${IMAGE_NAME}:${IMAGE_TAG}"

if ${PUSH}; then
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    REGION="${AWS_REGION:-us-east-1}"
    ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${IMAGE_NAME}"

    aws ecr describe-repositories --repository-names "${IMAGE_NAME}" --region "${REGION}" 2>/dev/null \
        || aws ecr create-repository --repository-name "${IMAGE_NAME}" --region "${REGION}"

    aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

    docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
    docker push "${ECR_URI}:${IMAGE_TAG}"
    echo "✅ Pushed to: ${ECR_URI}:${IMAGE_TAG}"
fi
