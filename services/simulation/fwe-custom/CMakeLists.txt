cmake_minimum_required(VERSION 3.10.2)
project(fwe-gps)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED True)

find_package(AwsIotFwe REQUIRED)
find_package(Boost REQUIRED COMPONENTS filesystem thread chrono system)

find_path(JSONCPP_INCLUDE_DIR "json/json.h" PATH_SUFFIXES "jsoncpp")
find_library(JSONCPP_LIBRARY NAMES jsoncpp)
find_path(SNAPPY_INCLUDE_DIR "snappy.h")
find_library(SNAPPY_LIBRARY NAMES snappy)
find_library(PROTOBUF_LIBRARY NAMES protobuf)
find_library(CURL_LIBRARY NAMES curl)

set(OLD_CMAKE_FIND_LIBRARY_SUFFIXES ${CMAKE_FIND_LIBRARY_SUFFIXES})
set(CMAKE_FIND_LIBRARY_SUFFIXES ".a")
find_library(AWS_CRT_CPP NAMES aws-crt-cpp)
find_library(AWS_C_MQTT NAMES aws-c-mqtt)
find_library(AWS_C_HTTP NAMES aws-c-http)
find_library(AWS_C_IO NAMES aws-c-io)
find_library(AWS_C_CAL NAMES aws-c-cal)
find_library(AWS_C_AUTH NAMES aws-c-auth)
find_library(AWS_C_COMMON NAMES aws-c-common)
find_library(AWS_C_COMPRESSION NAMES aws-c-compression)
find_library(AWS_C_S3 NAMES aws-c-s3)
find_library(AWS_C_SDKUTILS NAMES aws-c-sdkutils)
find_library(AWS_C_EVENT_STREAM NAMES aws-c-event-stream)
find_library(AWS_CHECKSUMS NAMES aws-checksums)
find_library(AWS_SDK_CORE NAMES aws-cpp-sdk-core)
find_library(AWS_SDK_IOT NAMES aws-cpp-sdk-iot)
find_library(S2N_LIBRARY NAMES s2n)
set(CMAKE_FIND_LIBRARY_SUFFIXES ${OLD_CMAKE_FIND_LIBRARY_SUFFIXES})

add_executable(fwe-gps gps-main.cpp)

target_compile_definitions(fwe-gps PRIVATE FWE_FEATURE_EXTERNAL_GPS)

target_include_directories(fwe-gps PRIVATE ${JSONCPP_INCLUDE_DIR})

target_link_libraries(fwe-gps
    AwsIotFwe::aws-iot-fleetwise-edge
    AwsIotFwe::fwe
    AwsIotFwe::fwe-proto
    ${SNAPPY_LIBRARY}
    ${JSONCPP_LIBRARY}
    ${PROTOBUF_LIBRARY}
    ${AWS_SDK_CORE}
    ${AWS_SDK_IOT}
    ${AWS_CRT_CPP}
    ${AWS_C_MQTT}
    ${AWS_C_HTTP}
    ${AWS_C_IO}
    ${AWS_C_S3}
    ${AWS_C_AUTH}
    ${AWS_C_CAL}
    ${AWS_C_COMPRESSION}
    ${AWS_C_SDKUTILS}
    ${AWS_C_EVENT_STREAM}
    ${AWS_CHECKSUMS}
    ${AWS_C_COMMON}
    ${S2N_LIBRARY}
    ${CURL_LIBRARY}
    Boost::filesystem
    Boost::thread
    Boost::chrono
    Boost::system
    ssl crypto z
    pthread dl rt
)
