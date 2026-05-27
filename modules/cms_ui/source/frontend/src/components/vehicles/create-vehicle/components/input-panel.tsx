// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, {
  useState,
  useImperativeHandle,
  forwardRef,
  useRef,
  useContext,
  useEffect,
} from "react";
import {
  Container,
  Input,
  FormField,
  SpaceBetween,
  Header,
  Checkbox,
  Alert,
  Button,
  Select,
} from "@cloudscape-design/components";
import { ApiContext } from "@/api/provider";
import { getRuntimeConfig } from "../../../../config/api";
import { FleetSelector } from "@/components/commons/FleetSelector";

interface SimpleCreateVehicleEntry {
  fleetId?: string;
  vin: string;
  make: string;
  model: string;
  year: number;
  licensePlate: string;
  color?: string;
  vehicleType?: string;
  fuelType?: string;
  tags?: any[];
  createCertificate?: boolean;
  certificateArn?: string;
  certificateId?: string;
}

export interface CreateVehicleInputPanelProps {
  loadHelpPanelContent: any;
  inputData: SimpleCreateVehicleEntry;
  setInputData: any;
}

export interface CreateVehicleInputPanelRef {
  validate: () => boolean;
}

export const CreateVehicleInputPanel = forwardRef<
  CreateVehicleInputPanelRef,
  CreateVehicleInputPanelProps
>(function CreateVehicleInputPanel(
  { loadHelpPanelContent, inputData, setInputData },
  ref,
) {
  const [errors, setErrors] = useState({
    vin: "",
    make: "",
    model: "",
    year: "",
    licensePlate: "",
    color: "",
    vehicleType: "",
    fuelType: "",
  });
  
  const api = useContext(ApiContext);

  const vinRef = useRef<HTMLDivElement>(null);
  const makeRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<HTMLDivElement>(null);
  const yearRef = useRef<HTMLDivElement>(null);
  const licensePlateRef = useRef<HTMLDivElement>(null);
  const colorRef = useRef<HTMLDivElement>(null);

  const validateVin = (value: string): string => {
    if (!value || value.trim() === "") {
      return "VIN is required.";
    }
    if (value.length !== 17) {
      return "VIN must be exactly 17 characters.";
    }
    if (!/^[A-HJ-NPR-Z0-9]{17}$/i.test(value)) {
      return "VIN contains invalid characters.";
    }
    return "";
  };

  const validateMake = (value: string): string => {
    if (!value || value.trim() === "") {
      return "Make is required.";
    }
    return "";
  };

  const validateModel = (value: string): string => {
    if (!value || value.trim() === "") {
      return "Model is required.";
    }
    return "";
  };

  const validateYear = (value: number): string => {
    const currentYear = new Date().getFullYear();
    if (!value || value < 1900 || value > currentYear + 1) {
      return `Year must be between 1900 and ${currentYear + 1}.`;
    }
    return "";
  };

  const validateLicensePlate = (value: string): string => {
    if (!value || value.trim() === "") {
      return "License Plate is required.";
    }
    return "";
  };

  // VIN generation function
  const generateRandomVIN = (): string => {
    // VIN characters (excluding I, O, Q)
    const vinChars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789";
    
    // Common manufacturer codes for realistic VINs
    const manufacturerCodes = [
      "1HG", // Honda (USA)
      "1FT", // Ford Truck (USA)
      "1GC", // General Motors Truck (USA)
      "2HG", // Honda (Canada)
      "3VW", // Volkswagen (Mexico)
      "4T1", // Toyota (USA)
      "5NP", // Hyundai (USA)
      "JHM", // Honda (Japan)
      "KMH", // Hyundai (Korea)
      "WBA", // BMW (Germany)
      "WDB", // Mercedes-Benz (Germany)
      "YV1", // Volvo (Sweden)
    ];
    
    // Generate VIN
    const manufacturer = manufacturerCodes[Math.floor(Math.random() * manufacturerCodes.length)];
    let vin = manufacturer;
    
    // Add remaining 14 characters
    for (let i = 0; i < 14; i++) {
      vin += vinChars[Math.floor(Math.random() * vinChars.length)];
    }
    
    return vin;
  };

  const handleRandomizeVIN = () => {
    const randomVIN = generateRandomVIN();
    setInputData({ vin: randomVIN });
    setErrors((prev) => ({ ...prev, vin: validateVin(randomVIN) }));
  };

  useImperativeHandle(ref, () => ({
    validate: () => {
      const vinError = validateVin(inputData.vin || "");
      const makeError = validateMake(inputData.make || "");
      const modelError = validateModel(inputData.model || "");
      const yearError = validateYear(inputData.year || 0);
      const licensePlateError = validateLicensePlate(inputData.licensePlate || "");

      setErrors({
        vin: vinError,
        make: makeError,
        model: modelError,
        year: yearError,
        licensePlate: licensePlateError,
      });

      if (vinError) {
        vinRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }
      if (makeError) {
        makeRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }
      if (modelError) {
        modelRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }
      if (yearError) {
        yearRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }
      if (licensePlateError) {
        licensePlateRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }

      return true;
    },
  }));

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Provide basic information about the vehicle you want to create."
        >
          Vehicle Information
        </Header>
      }
    >
      <SpaceBetween size="l">
        <FormField
          label="Fleet"
          description="Select the fleet this vehicle belongs to."
        >
          <FleetSelector
            selectedFleet={inputData.fleetId || null}
            onFleetChange={(fleetId) => {
              setInputData({ fleetId: fleetId || '' });
            }}
            label=""
            showAllOption={false}
          />
        </FormField>
        
        <div ref={vinRef}>
          <FormField
            label="VIN"
            description="Vehicle Identification Number (17 characters)."
            constraintText="Must be exactly 17 alphanumeric characters (excluding I, O, Q)."
            i18nStrings={{ errorIconAriaLabel: "Error" }}
            errorText={errors.vin}
          >
            <SpaceBetween direction="horizontal" size="xs">
              <Input
                ariaRequired={true}
                value={inputData.vin ?? ""}
                onChange={({ detail: { value } }) => {
                  const upperValue = value.toUpperCase();
                  setInputData({ vin: upperValue });
                  setErrors((prev) => ({ ...prev, vin: validateVin(upperValue) }));
                }}
                onBlur={() => {
                  const vin = inputData.vin || "";
                  setErrors((prev) => ({
                    ...prev,
                    vin: validateVin(vin),
                  }));
                }}
              />
              <Button
                variant="normal"
                iconName="refresh"
                onClick={handleRandomizeVIN}
                ariaLabel="Generate random VIN"
              >
                Randomize
              </Button>
            </SpaceBetween>
          </FormField>
        </div>

        <SpaceBetween size="m" direction="horizontal">
          <div ref={makeRef} style={{ flex: 1 }}>
            <FormField
              label="Make"
              description="Vehicle manufacturer."
              i18nStrings={{ errorIconAriaLabel: "Error" }}
              errorText={errors.make}
            >
              <Input
                ariaRequired={true}
                value={inputData.make ?? ""}
                onChange={({ detail: { value } }) => {
                  setInputData({ make: value });
                  setErrors((prev) => ({ ...prev, make: validateMake(value) }));
                }}
                onBlur={() => {
                  const make = inputData.make || "";
                  setErrors((prev) => ({
                    ...prev,
                    make: validateMake(make),
                  }));
                }}
              />
            </FormField>
          </div>

          <div ref={modelRef} style={{ flex: 1 }}>
            <FormField
              label="Model"
              description="Vehicle model."
              i18nStrings={{ errorIconAriaLabel: "Error" }}
              errorText={errors.model}
            >
              <Input
                ariaRequired={true}
                value={inputData.model ?? ""}
                onChange={({ detail: { value } }) => {
                  setInputData({ model: value });
                  setErrors((prev) => ({ ...prev, model: validateModel(value) }));
                }}
                onBlur={() => {
                  const model = inputData.model || "";
                  setErrors((prev) => ({
                    ...prev,
                    model: validateModel(model),
                  }));
                }}
              />
            </FormField>
          </div>
        </SpaceBetween>

        <SpaceBetween size="m" direction="horizontal">
          <div ref={yearRef} style={{ flex: 1 }}>
            <FormField
              label="Year"
              description="Vehicle model year."
              i18nStrings={{ errorIconAriaLabel: "Error" }}
              errorText={errors.year}
            >
              <Input
                ariaRequired={true}
                type="number"
                value={inputData.year?.toString() ?? ""}
                onChange={({ detail: { value } }) => {
                  const yearValue = parseInt(value) || 0;
                  setInputData({ year: yearValue });
                  setErrors((prev) => ({ ...prev, year: validateYear(yearValue) }));
                }}
                onBlur={() => {
                  const year = inputData.year || 0;
                  setErrors((prev) => ({
                    ...prev,
                    year: validateYear(year),
                  }));
                }}
              />
            </FormField>
          </div>

          <div ref={licensePlateRef} style={{ flex: 1 }}>
            <FormField
              label="License Plate"
              description="Vehicle license plate number."
              i18nStrings={{ errorIconAriaLabel: "Error" }}
              errorText={errors.licensePlate}
            >
              <Input
                ariaRequired={true}
                value={inputData.licensePlate ?? ""}
                onChange={({ detail: { value } }) => {
                  setInputData({ licensePlate: value });
                  setErrors((prev) => ({ ...prev, licensePlate: validateLicensePlate(value) }));
                }}
                onBlur={() => {
                  const licensePlate = inputData.licensePlate || "";
                  setErrors((prev) => ({
                    ...prev,
                    licensePlate: validateLicensePlate(licensePlate),
                  }));
                }}
              />
            </FormField>
          </div>
        </SpaceBetween>

        {/* Additional Vehicle Attributes */}
        <SpaceBetween direction="horizontal" size="s">
          <div ref={colorRef} style={{ flex: 1 }}>
            <FormField
              label="Color"
              description="Vehicle color."
            >
              <Select
                selectedOption={
                  inputData.color 
                    ? { label: inputData.color, value: inputData.color }
                    : null
                }
                onChange={({ detail }) => {
                  setInputData({ color: detail.selectedOption?.value || '' });
                }}
                options={[
                  { label: "White", value: "White" },
                  { label: "Black", value: "Black" },
                  { label: "Silver", value: "Silver" },
                  { label: "Blue", value: "Blue" },
                  { label: "Red", value: "Red" },
                  { label: "Gray", value: "Gray" },
                  { label: "Green", value: "Green" },
                ]}
                placeholder="Select color"
              />
            </FormField>
          </div>
          
          <div style={{ flex: 1 }}>
            <FormField
              label="Vehicle Type"
              description="Type of vehicle."
            >
              <Select
                selectedOption={
                  inputData.vehicleType 
                    ? { label: inputData.vehicleType, value: inputData.vehicleType }
                    : null
                }
                onChange={({ detail }) => {
                  setInputData({ vehicleType: detail.selectedOption?.value || '' });
                }}
                options={[
                  { label: "Sedan", value: "Sedan" },
                  { label: "SUV", value: "SUV" },
                  { label: "Van", value: "Van" },
                  { label: "Truck", value: "Truck" },
                  { label: "Pickup", value: "Pickup" },
                  { label: "Coupe", value: "Coupe" },
                ]}
                placeholder="Select vehicle type"
              />
            </FormField>
          </div>

          <div style={{ flex: 1 }}>
            <FormField
              label="Fuel Type"
              description="Vehicle fuel type."
            >
              <Select
                selectedOption={
                  inputData.fuelType 
                    ? { label: inputData.fuelType, value: inputData.fuelType }
                    : null
                }
                onChange={({ detail }) => {
                  setInputData({ fuelType: detail.selectedOption?.value || '' });
                }}
                options={[
                  { label: "Gasoline", value: "gasoline" },
                  { label: "Diesel", value: "diesel" },
                  { label: "Electric", value: "electric" },
                  { label: "Hybrid", value: "hybrid" },
                  { label: "Plug-in Hybrid", value: "plugin-hybrid" },
                ]}
                placeholder="Select fuel type"
              />
            </FormField>
          </div>
        </SpaceBetween>

        {/* Certificate Creation Section */}
        <Container
          header={
            <Header
              variant="h3"
              description="Configure IoT Core certificate for secure device authentication."
            >
              IoT Certificate Configuration
            </Header>
          }
        >
          <SpaceBetween size="m">
            <FormField
              label="IoT Device Certificate"
              description="Create a unique X.509 certificate for this vehicle to authenticate with AWS IoT Core."
            >
              <Checkbox
                checked={inputData.createCertificate || false}
                onChange={({ detail }) => {
                  setInputData({ createCertificate: detail.checked });
                }}
              >
                Create IoT Core certificate for this vehicle
              </Checkbox>
            </FormField>

            {inputData.createCertificate && (
              <Alert
                type="info"
                header="Certificate Creation"
              >
                A unique X.509 certificate will be created for this vehicle and stored securely. 
                This certificate will be used by the simulator to establish authenticated connections to AWS IoT Core.
                The certificate will be automatically activated and attached to the appropriate IoT policy.
              </Alert>
            )}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </Container>
  );
});
