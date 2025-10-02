import React, { createContext, useContext, useState } from 'react';

interface VehicleContextType {
  vehicleVin: string | null;
  setVehicleVin: (vin: string | null) => void;
  driverName: string | null;
  setDriverName: (name: string | null) => void;
}

const VehicleContext = createContext<VehicleContextType | undefined>(undefined);

export const VehicleProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [vehicleVin, setVehicleVin] = useState<string | null>(null);
  const [driverName, setDriverName] = useState<string | null>(null);

  return (
    <VehicleContext.Provider value={{ vehicleVin, setVehicleVin, driverName, setDriverName }}>
      {children}
    </VehicleContext.Provider>
  );
};

export const useVehicle = () => {
  const context = useContext(VehicleContext);
  if (context === undefined) {
    // Return default values when provider is not available
    return { vehicleVin: null, setVehicleVin: () => {}, driverName: null, setDriverName: () => {} };
  }
  return context;
};
