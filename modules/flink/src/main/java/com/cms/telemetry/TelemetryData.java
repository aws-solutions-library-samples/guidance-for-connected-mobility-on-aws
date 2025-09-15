package com.cms.telemetry;

import java.util.List;

public class TelemetryData {
    public String vehicleId;
    public String originalVehicleId;
    public double speed;
    public double engineTemp;
    public double fuelLevel;
    public String engineEvent;
    public String tripId;
    public String driverId;
    public double latitude;
    public double longitude;
    public double heading;
    public double engineRPM;
    public boolean ignitionOn;
    public long timestamp;
    public long processedTimestamp;
    public List<String> safetyAlerts;
    public List<String> maintenanceAlerts;
    
    // Setters
    public void setProcessedTimestamp(long timestamp) {
        this.processedTimestamp = timestamp;
    }
}
