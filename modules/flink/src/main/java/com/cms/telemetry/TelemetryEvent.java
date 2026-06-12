package com.cms.telemetry;

public class TelemetryEvent {
    public String vin;
    public long timestamp;
    public double speed;
    public double latitude;
    public double longitude;
    public double fuelLevel;
    public double engineTemp;
    
    public TelemetryEvent() {}
    
    public TelemetryEvent(String vin, long timestamp, double speed, double latitude, 
                         double longitude, double fuelLevel, double engineTemp) {
        this.vin = vin;
        this.timestamp = timestamp;
        this.speed = speed;
        this.latitude = latitude;
        this.longitude = longitude;
        this.fuelLevel = fuelLevel;
        this.engineTemp = engineTemp;
    }
    
    @Override
    public String toString() {
        return String.format("TelemetryEvent{vin='%s', ts=%d, spd=%.1f, lat=%.6f, lon=%.6f, fuel=%.1f, temp=%.1f}",
                vin, timestamp, speed, latitude, longitude, fuelLevel, engineTemp);
    }
}
