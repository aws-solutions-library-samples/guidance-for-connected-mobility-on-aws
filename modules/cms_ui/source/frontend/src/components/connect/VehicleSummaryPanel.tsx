import React, { useState, useEffect } from "react";
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  ColumnLayout,
  StatusIndicator,
  Spinner,
  Alert,
  Link,
  KeyValuePairs,
  Badge,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/useAuth";
import { getApiEndpoint } from "../../config/api";

interface VehicleInfo {
  vin: string;
  make?: string;
  model?: string;
  year?: string;
  status?: string;
  licensePlate?: string;
  mileage?: number;
  fuelLevel?: number;
  batteryLevel?: number;
  driverName?: string;
}

interface VehicleSummaryPanelProps {
  vin: string | null;
}

export const VehicleSummaryPanel: React.FC<VehicleSummaryPanelProps> = ({ vin }) => {
  const [vehicle, setVehicle] = useState<VehicleInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const auth = useAuth();

  useEffect(() => {
    if (!vin) {
      setVehicle(null);
      return;
    }

    const fetchVehicle = async () => {
      setLoading(true);
      setError(null);
      try {
        const apiBase = getApiEndpoint();
        const resp = await fetch(`${apiBase}api/v1/vehicles/${vin}`, {
          headers: {
            'Content-Type': 'application/json',
            ...auth.getAuthHeaders(),
          },
        });
        if (!resp.ok) throw new Error(`${resp.status}`);
        const data = await resp.json();
        setVehicle({
          vin,
          make: data.make,
          model: data.model,
          year: data.year,
          status: data.status,
          licensePlate: data.license_plate || data.licensePlate,
          mileage: data.mileage,
          fuelLevel: data.fuel_level || data.fuelLevel,
          batteryLevel: data.battery_level || data.batteryLevel,
          driverName: data.driver_name || data.driverName,
        });
      } catch (e: any) {
        setError(`Failed to load vehicle: ${e.message}`);
      } finally {
        setLoading(false);
      }
    };

    fetchVehicle();
  }, [vin]);

  if (!vin) return null;

  return (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            <Link onFollow={() => navigate(`/vehicles/management/${vin}`)}>Full Details →</Link>
          }
        >
          Vehicle
        </Header>
      }
    >
      {loading && (
        <Box textAlign="center" padding="l"><Spinner /> Loading vehicle data...</Box>
      )}
      {error && <Alert type="error">{error}</Alert>}
      {vehicle && !loading && (
        <KeyValuePairs
          columns={2}
          items={[
            { label: "VIN", value: vehicle.vin },
            { label: "Vehicle", value: [vehicle.year, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "—" },
            { label: "Status", value: vehicle.status ? <StatusIndicator type={vehicle.status === "active" ? "success" : "warning"}>{vehicle.status}</StatusIndicator> : "—" },
            { label: "License Plate", value: vehicle.licensePlate || "—" },
            { label: "Driver", value: vehicle.driverName || "—" },
            { label: "Battery", value: vehicle.batteryLevel != null ? `${vehicle.batteryLevel}%` : "—" },
          ]}
        />
      )}
    </Container>
  );
};
