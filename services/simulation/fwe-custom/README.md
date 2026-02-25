# Custom FWE with External GPS Support

Custom build of AWS IoT FleetWise Edge Agent (v1.3.2) with `-DFWE_FEATURE_EXTERNAL_GPS=On`.

GPS coordinates are fed via a Unix domain socket and injected into the same protobuf telemetry stream as CAN signals.

## Pre-built Image

```bash
docker pull public.ecr.aws/s0o2j8p0/cms-fwe-gps:latest
```

## Building from Source

Requires Docker with at least 6GB RAM and 4 CPUs (arm64 or amd64).

```bash
docker build --platform linux/arm64 -t cms-fwe-gps .
```

Build takes ~25 minutes (compiles FWE + all dependencies from source).

## GPS Socket Protocol

The binary listens on a Unix socket (default `/tmp/fwe-gps/gps.sock`, configurable via `FWE_GPS_SOCKET_PATH` env var).

Protocol: newline-delimited JSON:
```
{"lat":40.7128,"lng":-74.0060}\n
```

The simulator connects and writes coordinates each telemetry tick. FWE's `ExternalGpsSource` injects them as named signals (`Vehicle.CurrentLocation.Latitude`, `Vehicle.CurrentLocation.Longitude`) into the protobuf payload alongside CAN data.

## Architecture

```
gps-main.cpp
  ├── Starts IoTFleetWiseEngine (FWE as library)
  ├── Engine initializes ExternalGpsSource from config
  └── GPS listener thread
       ├── Creates Unix socket
       ├── Accepts connections
       ├── Parses JSON lines
       └── Calls ExternalGpsSource::setLocation(lat, lng)
```
