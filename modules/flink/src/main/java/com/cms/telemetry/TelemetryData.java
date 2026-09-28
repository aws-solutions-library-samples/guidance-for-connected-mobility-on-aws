package com.cms.telemetry;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public class TelemetryData {
    public String vehicleId;
    public String originalVehicleId;
    public String vin;
    public String source;
    public double speed;
    public double engineTemp;
    public double fuelLevel;
    public String engineEvent;
    public String tripId;
    public String driverId;
    public double heading;
    public double engineRPM;
    public boolean ignitionOn;
    public double odometer;
    public long timestamp;
    public long processedTimestamp;
    public List<String> safetyAlerts;
    public List<String> maintenanceAlerts;

    // Support both field name conventions for coordinates
    public double latitude;
    public double longitude;

    // Simulator format: flat lat/lng (may be string or number)
    @JsonProperty("lat")
    public void setLat(Object lat) {
        if (lat instanceof Number) {
            this.latitude = ((Number) lat).doubleValue();
        } else if (lat instanceof String) {
            try { this.latitude = Double.parseDouble((String) lat); } catch (Exception ignored) {}
        }
    }

    @JsonProperty("lng")
    public void setLng(Object lng) {
        if (lng instanceof Number) {
            this.longitude = ((Number) lng).doubleValue();
        } else if (lng instanceof String) {
            try { this.longitude = Double.parseDouble((String) lng); } catch (Exception ignored) {}
        }
    }

    // FWE format: nested location object { latitude, longitude }
    @JsonProperty("location")
    public void setLocation(Map<String, Object> location) {
        if (location != null) {
            Object lat = location.get("latitude");
            Object lng = location.get("longitude");
            if (lat instanceof Number) this.latitude = ((Number) lat).doubleValue();
            if (lng instanceof Number) this.longitude = ((Number) lng).doubleValue();
        }
    }

    public void setProcessedTimestamp(long timestamp) {
        this.processedTimestamp = timestamp;
    }
}
