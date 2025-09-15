#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# This script runs the Lambda packaging tests to ensure cms_common is properly included

set -e

SCRIPT_DIR=$(dirname "${BASH_SOURCE[0]}")
MODULE_PATH=$(cd "${SCRIPT_DIR}/.." && pwd)
SOLUTION_PATH=$(cd "${MODULE_PATH}/../../.." && pwd)

echo "Running Lambda packaging tests..."
cd "${MODULE_PATH}" && make test-lambda-packaging

echo "Lambda packaging tests completed successfully."