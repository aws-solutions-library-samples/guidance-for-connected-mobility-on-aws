// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import { Container, FormField, Header, Input, SpaceBetween } from '@cloudscape-design/components';
import { FleetSelector } from '@/components/commons/FleetSelector';

// Basic 17-char alphanumeric VIN pattern (excludes I, O, Q per industry standard)
const VIN_REGEX = /^[A-HJ-NPR-Z0-9]{17}$/i;
const DEFAULT_FLEET = 'oem1-staging-fleet';

export interface OEM1FormProps {
  vin: string;
  fleetId: string;
  onVinChange: (vin: string) => void;
  onFleetChange: (fleetId: string) => void;
  vinError?: string;
}

export const OEM1Form: React.FC<OEM1FormProps> = ({
  vin,
  fleetId,
  onVinChange,
  onFleetChange,
  vinError,
}) => {
  const [touched, setTouched] = useState(false);

  const localVinError =
    touched && !VIN_REGEX.test(vin)
      ? 'VIN must be exactly 17 alphanumeric characters (I, O, Q not allowed).'
      : '';

  const displayError = vinError ?? localVinError;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Enroll an OEM1 vehicle by VIN. Select the destination fleet."
        >
          OEM1 Vehicle Enrollment
        </Header>
      }
    >
      <SpaceBetween size="l">
        <FormField
          label={
            <>
              VIN <i>(required)</i>
            </>
          }
          description="17-character Vehicle Identification Number."
          constraintText="Exactly 17 alphanumeric characters — I, O, and Q are not valid VIN characters."
          errorText={displayError}
          i18nStrings={{ errorIconAriaLabel: 'Error' }}
        >
          <Input
            ariaRequired
            value={vin}
            placeholder="Enter 17-character VIN"
            onChange={({ detail }) => onVinChange(detail.value.toUpperCase())}
            onBlur={() => setTouched(true)}
          />
        </FormField>

        <FleetSelector
          selectedFleet={fleetId || DEFAULT_FLEET}
          onFleetChange={onFleetChange}
          label="Fleet"
          showAllOption={false}
        />
      </SpaceBetween>
    </Container>
  );
};

export function validateOEM1Vin(vin: string): string {
  if (!vin || vin.trim() === '') return 'VIN is required.';
  if (!VIN_REGEX.test(vin))
    return 'VIN must be exactly 17 alphanumeric characters (I, O, Q not allowed).';
  return '';
}
