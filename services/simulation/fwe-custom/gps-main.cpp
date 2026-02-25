// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Custom FWE main with external GPS support.
// Starts the FWE engine and listens on a Unix domain socket for GPS updates.
// GPS feeder protocol: newline-delimited JSON: {"lat":40.7128,"lng":-74.0060}\n

#include "aws/iotfleetwise/IoTFleetWiseEngine.h"
#include "aws/iotfleetwise/ConsoleLogger.h"
#include "aws/iotfleetwise/LoggingModule.h"

#include <boost/filesystem.hpp>
#include <json/json.h>
#include <fstream>
#include <iostream>
#include <thread>
#include <atomic>
#include <cstring>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *getGpsSocketPath()
{
    const char *env = std::getenv("FWE_GPS_SOCKET_PATH");
    return env ? env : "/tmp/fwe-gps/gps.sock";
}

static std::atomic<bool> gRunning{true};

static void gpsListenerThread(std::shared_ptr<Aws::IoTFleetWise::ExternalGpsSource> gpsSource)
{
    const char *socketPath = getGpsSocketPath();
    unlink(socketPath);

    int serverFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (serverFd < 0)
    {
        std::cerr << "GPS: Failed to create socket" << std::endl;
        return;
    }

    struct sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socketPath, sizeof(addr.sun_path) - 1);

    if (bind(serverFd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0)
    {
        std::cerr << "GPS: Failed to bind socket" << std::endl;
        close(serverFd);
        return;
    }

    listen(serverFd, 1);
    // Allow simulator to connect
    chmod(socketPath, 0777);
    std::cout << "GPS: Listening on " << socketPath << std::endl;

    while (gRunning)
    {
        // Accept with timeout so we can check gRunning
        fd_set readfds;
        FD_ZERO(&readfds);
        FD_SET(serverFd, &readfds);
        struct timeval tv{1, 0};
        if (select(serverFd + 1, &readfds, nullptr, nullptr, &tv) <= 0)
        {
            continue;
        }

        int clientFd = accept(serverFd, nullptr, nullptr);
        if (clientFd < 0)
        {
            continue;
        }
        std::cout << "GPS: Client connected" << std::endl;

        std::string buffer;
        char chunk[256];
        while (gRunning)
        {
            ssize_t n = read(clientFd, chunk, sizeof(chunk) - 1);
            if (n <= 0)
            {
                break;
            }
            chunk[n] = '\0';
            buffer.append(chunk);

            // Process complete lines
            size_t pos;
            while ((pos = buffer.find('\n')) != std::string::npos)
            {
                std::string line = buffer.substr(0, pos);
                buffer.erase(0, pos + 1);

                if (line.empty())
                {
                    continue;
                }

                Json::Value root;
                Json::CharReaderBuilder rb;
                std::istringstream ss(line);
                std::string errs;
                if (Json::parseFromStream(rb, ss, &root, &errs))
                {
                    double lat = root["lat"].asDouble();
                    double lng = root["lng"].asDouble();
                    gpsSource->setLocation(lat, lng);
                }
            }
        }
        close(clientFd);
        std::cout << "GPS: Client disconnected" << std::endl;
    }

    close(serverFd);
    unlink(socketPath);
}

int main(int argc, char *argv[])
{
    // Expect config file as first argument
    if (argc < 2)
    {
        std::cerr << "Usage: " << argv[0] << " <config.json>" << std::endl;
        return 1;
    }

    Aws::IoTFleetWise::IoTFleetWiseEngine::configureSignalHandlers();

    // Read config
    std::ifstream configFile(argv[1]);
    Json::Value config;
    Json::CharReaderBuilder rb;
    std::string errs;
    if (!Json::parseFromStream(rb, configFile, &config, &errs))
    {
        std::cerr << "Failed to parse config: " << errs << std::endl;
        return 1;
    }

    Aws::IoTFleetWise::IoTFleetWiseEngine::configureLogging(config);
    std::cout << Aws::IoTFleetWise::IoTFleetWiseEngine::getVersion() << std::endl;

    Aws::IoTFleetWise::IoTFleetWiseEngine engine;

    boost::filesystem::path configDir = boost::filesystem::path(argv[1]).parent_path();
    if (!engine.connect(config, configDir))
    {
        std::cerr << "Failed to connect engine" << std::endl;
        return 1;
    }

    // Start GPS listener if external GPS source was configured
    std::thread gpsThread;
    if (engine.mExternalGpsSource)
    {
        gpsThread = std::thread(gpsListenerThread, engine.mExternalGpsSource);
        std::cout << "GPS: External GPS source enabled" << std::endl;
    }
    else
    {
        std::cerr << "WARNING: No externalGpsInterface in config, GPS socket disabled" << std::endl;
    }

    if (!engine.start())
    {
        std::cerr << "Failed to start engine" << std::endl;
        return 1;
    }

    // Wait for signal
    while (Aws::IoTFleetWise::gSignal == 0)
    {
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    gRunning = false;
    int exitCode = Aws::IoTFleetWise::IoTFleetWiseEngine::signalToExitCode(
        Aws::IoTFleetWise::gSignal);

    if (!engine.stop())
    {
        std::cerr << "Failed to stop engine" << std::endl;
        return 1;
    }
    if (!engine.disconnect())
    {
        std::cerr << "Failed to disconnect engine" << std::endl;
        return 1;
    }

    if (gpsThread.joinable())
    {
        gpsThread.join();
    }

    return exitCode;
}
