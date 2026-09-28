package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;

/**
 * Transforms CMS telemetry data from wide format (one record per vehicle)
 * to long format (one record per tire) for tire prediction model.
 * 
 * Input: CMS telemetry JSON with tire_fl, tire_fr, tire_rl, tire_rr
 * Output: Multiple TireTelemetryRecord objects (one per tire position)
 */
public class TireTelemetryTransformer implements FlatMapFunction<String, TireTelemetryTransformer.TireTelemetryRecord> {
    
    private static final Logger LOG = LoggerFactory.getLogger(TireTelemetryTransformer.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final DateTimeFormatter ISO_FORMATTER = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ssX");
    
    // Tire positions
    private static final String[] TIRE_POSITIONS = {"FL", "FR", "RL", "RR"};
    
    @Override
    public void flatMap(String rawTelemetry, Collector<TireTelemetryRecord> out) throws Exception {
        try {
            JsonNode json = objectMapper.readTree(rawTelemetry);
            
            // Extract common fields
            String vehicleId = json.has("vehicleId") ? json.get("vehicleId").asText() : null;
            long timestamp = json.has("timestamp") ? json.get("timestamp").asLong() : System.currentTimeMillis();
            
            if (vehicleId == null || vehicleId.isEmpty()) {
                LOG.warn("Skipping record with missing vehicleId");
                return;
            }
            
            // Extract location
            double latitude = 0.0;
            double longitude = 0.0;
            if (json.has("location")) {
                JsonNode location = json.get("location");
                latitude = location.has("latitude") ? location.get("latitude").asDouble() : 0.0;
                longitude = location.has("longitude") ? location.get("longitude").asDouble() : 0.0;
            } else {
                latitude = json.has("latitude") ? json.get("latitude").asDouble() : 0.0;
                longitude = json.has("longitude") ? json.get("longitude").asDouble() : 0.0;
            }
            
            // Extract tire temperature (single value for all tires in CMS)
            double tireTempF = json.has("tire_temp_max") ? json.get("tire_temp_max").asDouble() : 100.0;
            
            // Transform each tire position
            for (String position : TIRE_POSITIONS) {
                String tireKey = "tire_" + position.toLowerCase();
                String treadKey = "tire_tread_" + position.toLowerCase();
                
                if (!json.has(tireKey)) {
                    continue; // Skip if tire data not present
                }
                
                double pressurePsi = json.get(tireKey).asDouble();
                double treadDepthMm = json.has(treadKey) ? json.get(treadKey).asDouble() : 0.0;
                
                // Create tire record
                TireTelemetryRecord tireRecord = new TireTelemetryRecord();
                tireRecord.aaid = vehicleId;
                tireRecord.deviceId = vehicleId;
                tireRecord.eventTimestamp = formatTimestamp(timestamp);
                tireRecord.timestampEpoch = timestamp;
                tireRecord.tpmsAvmTirePosition = position;
                tireRecord.tpmsPressureInMbar = convertPsiToMbar(pressurePsi);
                tireRecord.tpmsTireTemperatureInCelsius = convertFahrenheitToCelsius(tireTempF);
                tireRecord.treadDepthMm = treadDepthMm;
                tireRecord.latitude = latitude;
                tireRecord.longitude = longitude;
                tireRecord.assetType = "vehicle";
                tireRecord.assetId = vehicleId;
                
                // Calculate tire condition based on pressure
                tireRecord.tpmsCondition = calculateTireCondition(pressurePsi);
                
                // Partitioning fields
                Instant instant = Instant.ofEpochMilli(timestamp);
                tireRecord.year = instant.atZone(ZoneOffset.UTC).getYear();
                tireRecord.month = instant.atZone(ZoneOffset.UTC).getMonthValue();
                tireRecord.day = instant.atZone(ZoneOffset.UTC).getDayOfMonth();
                tireRecord.hour = instant.atZone(ZoneOffset.UTC).getHour();
                
                out.collect(tireRecord);
            }
            
        } catch (Exception e) {
            LOG.error("Error transforming tire telemetry: {}", e.getMessage(), e);
        }
    }
    
    /**
     * Convert PSI to millibar
     * 1 PSI = 68.9476 mbar
     */
    private double convertPsiToMbar(double psi) {
        return psi * 68.9476;
    }
    
    /**
     * Convert Fahrenheit to Celsius
     * C = (F - 32) * 5/9
     */
    private double convertFahrenheitToCelsius(double fahrenheit) {
        return (fahrenheit - 32.0) * 5.0 / 9.0;
    }
    
    /**
     * Calculate tire condition based on pressure
     * Normal: 28-35 PSI
     * Warning: 25-28 or 35-40 PSI
     * Critical: <25 or >40 PSI
     */
    private String calculateTireCondition(double pressurePsi) {
        if (pressurePsi < 25.0 || pressurePsi > 40.0) {
            return "CRITICAL";
        } else if (pressurePsi < 28.0 || pressurePsi > 35.0) {
            return "WARNING";
        } else {
            return "NORMAL";
        }
    }
    
    /**
     * Format timestamp to ISO 8601 format with timezone
     * Example: "2024-10-03 16:00:00+00:00"
     */
    private String formatTimestamp(long epochMillis) {
        Instant instant = Instant.ofEpochMilli(epochMillis);
        return instant.atZone(ZoneOffset.UTC).format(ISO_FORMATTER);
    }
    
    /**
     * Tire telemetry record in long format (one record per tire)
     * Matches the tire prediction model schema
     */
    public static class TireTelemetryRecord {
        public String deviceId;              // Vehicle ID
        public String eventTimestamp;        // ISO 8601 timestamp
        public long timestampEpoch;          // Unix timestamp (ms)
        public String aaid;                  // Vehicle/trailer ID
        public String assetType;             // "vehicle"
        public String assetId;               // Vehicle ID
        public String tpmsAvmTirePosition;   // FL, FR, RL, RR
        public double tpmsPressureInMbar;    // Tire pressure in millibar
        public String tpmsCondition;         // NORMAL, WARNING, CRITICAL
        public double tpmsTireTemperatureInCelsius; // Temperature in Celsius
        public double treadDepthMm;          // Tread depth in millimeters
        public double latitude;              // GPS latitude
        public double longitude;             // GPS longitude
        
        // Partitioning fields
        public int year;
        public int month;
        public int day;
        public int hour;
        
        @Override
        public String toString() {
            return String.format(
                "TireTelemetry{aaid=%s, position=%s, pressure=%.1f mbar, temp=%.1f°C, tread=%.1f mm, condition=%s, time=%s}",
                aaid, tpmsAvmTirePosition, tpmsPressureInMbar, 
                tpmsTireTemperatureInCelsius, treadDepthMm, tpmsCondition, eventTimestamp
            );
        }
    }
}
