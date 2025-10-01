#!/bin/bash

FRONTEND_DIR="/Users/givenand/connected-mobility-workspace/modules/cms_ui/source/frontend"
PUBLIC_DIR="$FRONTEND_DIR/public"
BUILD_DIR="$FRONTEND_DIR/build"

if [ -z "$1" ]; then
    echo "Usage: $0 <profile-name>"
    echo "Available profiles:"
    ls "$PUBLIC_DIR"/runtimeConfig.*.json 2>/dev/null | sed 's/.*runtimeConfig\.\(.*\)\.json/  \1/'
    exit 1
fi

PROFILE="$1"
SOURCE_CONFIG="$PUBLIC_DIR/runtimeConfig.$PROFILE.json"

if [ ! -f "$SOURCE_CONFIG" ]; then
    echo "Error: Profile '$PROFILE' not found. Available profiles:"
    ls "$PUBLIC_DIR"/runtimeConfig.*.json 2>/dev/null | sed 's/.*runtimeConfig\.\(.*\)\.json/  \1/'
    exit 1
fi

# Copy to main runtime config
cp "$SOURCE_CONFIG" "$PUBLIC_DIR/runtimeConfig.json"

# Copy to build directory if it exists
if [ -d "$BUILD_DIR" ]; then
    cp "$SOURCE_CONFIG" "$BUILD_DIR/runtimeConfig.json"
fi

echo "Switched to profile: $PROFILE"
echo "Please refresh your browser to apply changes."
