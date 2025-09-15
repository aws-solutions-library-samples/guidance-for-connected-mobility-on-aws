# Guidance for Connected Mobility on AWS

A comprehensive AWS-based connected vehicle system featuring real-time telemetry processing, fleet management, and IoT device monitoring.

## Architecture Overview

```
connected-mobility-workspace/
├── cdk-stacks           # Infrastructure/           # AWS CDK Infrastructure Stacks
├── modules/              # Core Application Modules
│   ├── cms_ui           # Fleet Manager Interface/          # Fleet Management Web Interface
│   ├── deployment/      # Legacy Deployment Scripts
│   └── flink/           # Real-time Data Processing
├── services/            # Supporting Services
│   └── simulation/      # Vehicle Telemetry Simulation
├── lib/                 # Shared Libraries
├── scripts/             # Utility Scripts
└── documentation/       # All Documentation

```

## Quick Start

### Prerequisites
- Node.js 18+
- Python 3.9+
- Java 11+
- AWS CLI configured
- AWS CDK CLI installed

### Initial Setup
```bash
# Clone and setup workspace
git clone <repository-url>
cd connected-mobility-workspace

# Install dependencies (see individual module READMEs)
```

## Components

| Component | Description | Technology Stack |
|-----------|-------------|------------------|
| **Infrastructure** | Infrastructure as Code | AWS CDK, Python |
| **Fleet Manager** | Fleet Management Interface | React, TypeScript, Python |
| **Telemetry Pipeline** | Real-time Telemetry Processing | Apache Flink, Java |
| **Telemetry Simulation Service** | Vehicle Data Simulation | Python, AWS IoT |

## Documentation

All documentation has been organized in the `/documentation` folder:

- **[Infrastructure](./cdk-stacks/README.md)** - Infrastructure deployment
- **[Fleet Manager](./modules/fleet-manager/README.md)** - Web interface setup
- **[Telemetry Pipeline](./modules/flink/README.md)** - Data processing pipeline
- **[Telemetry Simulation Service](./services/simulation/README.md)** - Vehicle simulation
- **[Detailed Docs](./documentation/)** - Complete documentation archive

## Development Workflow

1. **Infrastructure**: Deploy CDK stacks first
2. **Backend Services**: Setup Flink processing and simulation
3. **Frontend**: Deploy Fleet Manager interface
4. **Testing**: Use simulation service for end-to-end testing

## Support

Refer to individual component READMEs for detailed setup instructions and troubleshooting guides.
