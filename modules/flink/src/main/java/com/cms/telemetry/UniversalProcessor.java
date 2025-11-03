package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.java.utils.ParameterTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;
import java.util.Properties;

/**
 * Universal Processor - Main entry point for all CMS telemetry processors.
 * Routes to specific processors based on PROCESSOR_TYPE environment variable.
 */
public class UniversalProcessor {
    private static final Logger LOG = LoggerFactory.getLogger(UniversalProcessor.class);

    public static void main(String[] args) throws Exception {
        // Enhanced logging for visibility
        LOG.error("=== UNIVERSAL PROCESSOR STARTING (ERROR LEVEL) ===");
        LOG.warn("=== UNIVERSAL PROCESSOR STARTING (WARN LEVEL) ===");
        LOG.info("=== UNIVERSAL PROCESSOR STARTING ===");
        
        System.out.println("=== UNIVERSAL PROCESSOR STARTING ===");

        // Get application properties
        Map<String, Properties> applicationProperties;
        if (args.length > 0) {
            ParameterTool params = ParameterTool.fromArgs(args);
            applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
        } else {
            applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
        }

        Properties consumerConfig = applicationProperties.get("consumer.config.0");
        if (consumerConfig == null) {
            throw new RuntimeException("Consumer configuration not found");
        }

        // Get processor type from environment
        String processorType = consumerConfig.getProperty("PROCESSOR_TYPE", "EventDrivenTelemetryProcessor");
        LOG.info("✅ Found PROCESSOR_TYPE: {}", processorType);
        System.out.println("✅ Found PROCESSOR_TYPE: " + processorType);

        // Log all available properties for debugging
        LOG.info("Available properties: {}", consumerConfig.stringPropertyNames());
        for (String key : consumerConfig.stringPropertyNames()) {
            if (!key.toLowerCase().contains("password") && !key.toLowerCase().contains("secret")) {
                LOG.info("Property: {} = {}", key, consumerConfig.getProperty(key));
            }
        }

        LOG.info("Processor Type: {}", processorType);
        System.out.println("Processor Type: " + processorType);

        // Route to appropriate processor based on type
        switch (processorType) {
            case "EventDrivenTelemetryProcessor":
                LOG.info("🔧 Routing to EventDrivenTelemetryProcessor...");
                System.out.println("🔧 Routing to EventDrivenTelemetryProcessor...");
                EventDrivenTelemetryProcessor.execute(args);
                break;
                
            case "TelemetryDataProcessor":
                LOG.info("🔧 Routing to TelemetryDataProcessor...");
                System.out.println("🔧 Routing to TelemetryDataProcessor...");
                TelemetryProcessor.execute(args);
                break;
                
            case "TripProcessor":
                LOG.info("🔧 Routing to TripProcessor...");
                System.out.println("🔧 Routing to TripProcessor...");
                TripProcessor.main(args);
                break;
                
            case "SafetyProcessor":
                LOG.info("🔧 Routing to SafetyProcessor...");
                System.out.println("🔧 Routing to SafetyProcessor...");
                SafetyProcessor.main(args);
                break;
                
            case "MaintenanceProcessor":
                LOG.info("🔧 Routing to MaintenanceProcessor...");
                System.out.println("🔧 Routing to MaintenanceProcessor...");
                MaintenanceProcessor.main(args);
                break;
                
            case "OEMTelemetryProcessor":
                LOG.info("🔧 Routing to OEMTelemetryProcessor...");
                System.out.println("🔧 Routing to OEMTelemetryProcessor...");
                OEMTelemetryProcessor.main(args);
                break;
                
            default:
                String errorMsg = "❌ Unknown processor type: " + processorType + 
                    ". Valid types: EventDrivenTelemetryProcessor, TelemetryDataProcessor, TripProcessor, SafetyProcessor, MaintenanceProcessor, OEMTelemetryProcessor";
                LOG.error(errorMsg);
                System.err.println(errorMsg);
                throw new IllegalArgumentException(errorMsg);
        }
    }
}
