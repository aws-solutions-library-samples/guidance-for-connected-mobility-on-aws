#!/bin/bash
# Start FWE with external GPS support
# Same as start-fwe.sh but uses fwe-gps binary and adds externalGpsInterface
set -euo pipefail

CONFIG_FILE="/etc/aws-iot-fleetwise/config-0.json"
mkdir -p $(dirname ${CONFIG_FILE})

/usr/bin/configure-fwe.sh \
    --input-config-file /usr/share/aws-iot-fleetwise/static-config.json \
    --output-config-file ${CONFIG_FILE} \
    "$@"

# Inject externalGpsInterface into networkInterfaces
GPS_INTERFACE='{"interfaceId":"GPS","type":"externalGpsInterface","externalGpsInterface":{"latitudeSignalName":"Vehicle.CurrentLocation.Latitude","longitudeSignalName":"Vehicle.CurrentLocation.Longitude"}}'
jq ".networkInterfaces += [${GPS_INTERFACE}]" ${CONFIG_FILE} > ${CONFIG_FILE}.tmp && mv ${CONFIG_FILE}.tmp ${CONFIG_FILE}

CAN_IF=$(jq -r '.networkInterfaces[0].canInterface.interfaceName // empty' ${CONFIG_FILE})
PERSISTENCY_PATH=$(jq -r '.staticConfig.persistency.persistencyPath' ${CONFIG_FILE})
mkdir -p ${PERSISTENCY_PATH}

if [ -n "${CAN_IF}" ]; then
    while true; do
        if ip link show "${CAN_IF}" up 2>/dev/null | grep -q UP; then
            break
        fi
        echo "Waiting for ${CAN_IF}"
        sleep 3
    done
fi

exec /usr/bin/fwe-gps ${CONFIG_FILE}
