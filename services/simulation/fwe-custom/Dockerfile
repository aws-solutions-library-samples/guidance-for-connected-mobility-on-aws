# Custom FWE build with External GPS support
# Multi-stage: build FWE from source, then create minimal runtime image

FROM public.ecr.aws/ubuntu/ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

# Install build deps
RUN apt-get update && apt-get install -y \
    build-essential cmake git wget unzip \
    libssl-dev libsnappy-dev zlib1g-dev \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Clone FWE at v1.3.2
RUN git clone --depth 1 --branch v1.3.2 https://github.com/aws/aws-iot-fleetwise-edge.git fwe

# Install FWE dependencies (limit parallelism to fit in 6GB)
RUN cd fwe && sed -i 's/make install -j`nproc`/make install -j3/g' tools/install-deps-native.sh \
    && ./tools/install-deps-native.sh --prefix /usr/local

# Build FWE as a LIBRARY with external GPS enabled
RUN cd fwe && mkdir build && cd build && \
    cmake \
      -DFWE_STATIC_LINK=On \
      -DFWE_SECURITY_COMPILE_FLAGS=On \
      -DFWE_FEATURE_EXTERNAL_GPS=On \
      -DFWE_BUILD_EXECUTABLE=Off \
      -DCMAKE_PREFIX_PATH=/usr/local \
      -DCMAKE_INSTALL_PREFIX=/usr/local \
      .. && \
    make -j3 && \
    make install

# Build our custom main that links against the FWE library
COPY gps-main.cpp CMakeLists.txt /build/custom/

RUN cd /build/custom && mkdir build && cd build && \
    cmake -DCMAKE_PREFIX_PATH=/usr/local -DFWE_STATIC_LINK=On .. && \
    make -j3

# Runtime image
FROM public.ecr.aws/ubuntu/ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates iproute2 jq libsnappy1v5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/custom/build/fwe-gps /usr/bin/fwe-gps
COPY --from=builder /build/fwe/configuration/static-config.json /usr/share/aws-iot-fleetwise/
COPY --from=builder /build/fwe/tools/configure-fwe.sh /usr/bin/
COPY --from=builder /build/fwe/tools/container/start-fwe.sh /usr/bin/

# Custom entrypoint that uses our GPS-enabled binary
COPY start-fwe-gps.sh /usr/bin/
RUN chmod +x /usr/bin/start-fwe-gps.sh /usr/bin/configure-fwe.sh /usr/bin/start-fwe.sh

ENTRYPOINT ["/usr/bin/start-fwe-gps.sh"]
