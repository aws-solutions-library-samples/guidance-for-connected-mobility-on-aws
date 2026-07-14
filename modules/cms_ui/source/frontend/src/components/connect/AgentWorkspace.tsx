import React, { useState, useEffect } from "react";
import { Grid, SpaceBetween, Box, Spinner, Alert } from "@cloudscape-design/components";
import { ConnectCCP } from "./ConnectCCP";
import { HandoverContextPanel } from "./HandoverContextPanel";
import VehicleDetailView from "../vehicles/vehicle-detail/VehicleDetailView";
import { getApiEndpoint } from "../../config/api";
import { useAuth } from "../../auth/useAuth";

const CONNECT_INSTANCE_URL = "https://cms-vsa-demo-use1.my.connect.aws/ccp-v2";

// VIN test per ISO 3779 (17 alphanumeric chars; I, O, Q excluded). The
// internal vehicleId format is "VEH-####", so the dash is a reliable
// disambiguator — anything matching this regex is a VIN, not a vehicleId.
const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/;
const looksLikeVin = (val: string): boolean => VIN_PATTERN.test(val.toUpperCase());

const AgentWorkspace: React.FC = () => {
  const [activeContact, setActiveContact] = useState<connect.Contact | null>(null);
  const [activeVin, setActiveVin] = useState<string | null>(null);

  // Resolved internal vehicleId (e.g. "VEH-0047") for the active contact's
  // vehicle. Connect contact attributes give us the customer's VIN, but the
  // backend's /api/v1/vehicles/{id} endpoint keys on vehicleId. Without
  // resolving first, VehicleDetailView fires fetch("…/api/v1/vehicles/<VIN>")
  // and the API returns 404 because the table is keyed on vehicleId.
  const [resolvedVehicleId, setResolvedVehicleId] = useState<string | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const auth = useAuth();

  const handleContactConnecting = (contact: connect.Contact) => {
    setActiveContact(contact);
    const attrs = contact.getAttributes();
    setActiveVin(attrs?.["vin"]?.value || null);
  };

  const handleContactEnded = () => {
    setActiveContact(null);
    setActiveVin(null);
  };

  // Resolve VIN -> vehicleId whenever the active contact's VIN changes. If
  // the upstream value already looks like an internal vehicleId (e.g. an
  // operator manually staged the workspace from a vehicle row), pass it
  // through without a lookup. The list endpoint's "search" query does a
  // CONTAINS match on vin/make/model, so we filter client-side for an
  // exact VIN match to avoid false positives (e.g. a VIN that happens to
  // be a substring of another model name).
  useEffect(() => {
    if (!activeVin) {
      setResolvedVehicleId(null);
      setResolveError(null);
      setResolving(false);
      return;
    }

    if (!looksLikeVin(activeVin)) {
      // Already looks like an internal vehicleId — pass straight through.
      setResolvedVehicleId(activeVin);
      setResolveError(null);
      setResolving(false);
      return;
    }

    let cancelled = false;
    setResolving(true);
    setResolveError(null);
    setResolvedVehicleId(null);

    (async () => {
      try {
        const apiBase = getApiEndpoint();
        const url = `${apiBase}api/v1/vehicles?search=${encodeURIComponent(activeVin)}&limit=10`;
        const resp = await fetch(url, {
          headers: {
            "Content-Type": "application/json",
            ...auth.getAuthHeaders(),
          },
        });
        if (!resp.ok) {
          throw new Error(`Lookup failed (${resp.status} ${resp.statusText})`);
        }
        const data = await resp.json();
        const vehicles: any[] = data.vehicles || data.items || [];
        const exact = vehicles.find((v: any) => (v.vin || "").toUpperCase() === activeVin.toUpperCase());
        if (cancelled) return;
        if (!exact || !exact.vehicleId) {
          setResolveError(
            `No fleet vehicle is registered with VIN ${activeVin}. ` +
              `Check the contact's VIN attribute in Connect or the vehicles table.`
          );
          return;
        }
        setResolvedVehicleId(exact.vehicleId);
      } catch (e: any) {
        if (cancelled) return;
        setResolveError(e?.message || "Failed to look up vehicle by VIN");
      } finally {
        if (!cancelled) setResolving(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeVin]);

  return (
    <SpaceBetween size="l">
      <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
        <ConnectCCP
          connectInstanceUrl={CONNECT_INSTANCE_URL}
          onContactConnecting={handleContactConnecting}
          onContactEnded={handleContactEnded}
        />
        <HandoverContextPanel contact={activeContact} />
      </Grid>

      {activeVin && resolving && (
        <Box textAlign="center" padding="l">
          <SpaceBetween size="s" direction="horizontal">
            <Spinner />
            <span>Looking up vehicle by VIN {activeVin}…</span>
          </SpaceBetween>
        </Box>
      )}
      {activeVin && resolveError && (
        <Alert type="error" header="Could not load vehicle for this contact">
          {resolveError}
        </Alert>
      )}
      {resolvedVehicleId && <VehicleDetailView vehicleIdProp={resolvedVehicleId} />}
    </SpaceBetween>
  );
};

export default AgentWorkspace;
