// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  DatePicker,
  FormField,
  Header,
  Modal,
  RadioGroup,
  Select,
  SpaceBetween,
  StatusIndicator,
  Textarea,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../../config/api";
import { useAuth } from "../../../auth/useAuth";

interface ScheduleServiceModalProps {
  visible: boolean;
  onDismiss: () => void;
  vehicleId: string;
  vin?: string;
  selectedAlerts: Array<{ alertType: string; severity: string; description?: string; message?: string; estimatedCost?: number }>;
}

const serviceProviders = [
  { value: "rush-dallas", label: "Rush Truck Center — Dallas", type: "Service Center", distance: "2.4 mi", nextAvailable: "Apr 2, 2026" },
  { value: "penske-denver", label: "Penske Truck Leasing — Denver", type: "Service Center", distance: "5.1 mi", nextAvailable: "Apr 3, 2026" },
  { value: "mobile-fleet", label: "FleetPro Mobile Service", type: "Mobile", distance: "On-site", nextAvailable: "Apr 1, 2026" },
  { value: "ta-petro", label: "TA Petro — Phoenix", type: "Service Center", distance: "12.3 mi", nextAvailable: "Apr 4, 2026" },
  { value: "dealer-ford", label: "AutoNation Ford — Houston", type: "Dealer", distance: "8.7 mi", nextAvailable: "Apr 5, 2026" },
];

const ScheduleServiceModal: React.FC<ScheduleServiceModalProps> = ({
  visible, onDismiss, vehicleId, vin, selectedAlerts
}) => {
  const [serviceType, setServiceType] = useState("service-center");
  const [selectedProvider, setSelectedProvider] = useState<any>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { getAuthHeaders } = useAuth();
  const apiEndpoint = getApiEndpoint();

  const totalEstCost = selectedAlerts.reduce((s, a) => s + (a.estimatedCost || 0), 0);

  const handleSubmit = async () => {
    if (!selectedProvider || !selectedDate || !vehicleId) return;
    setSubmitting(true);
    setError(null);
    try {
      // One service-history row per alert so each gets its own audit trail
      // and can be individually tracked. If no alerts were selected (e.g.,
      // ad-hoc scheduling), we still create a single row with a generic
      // service type.
      const serviceDate = selectedDate;  // YYYY-MM-DD from DatePicker
      const alertsForRecord = selectedAlerts.length > 0
        ? selectedAlerts
        : [{ alertType: "SCHEDULED_SERVICE", description: notes || "Scheduled service", severity: "MEDIUM" as const }];

      // POST one service-history row per alert.  Backend route:
      // POST /api/v1/service-history  {entry: <service_record>}
      for (const alert of alertsForRecord) {
        const serviceId = `SVC-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const record = {
          vehicleId,
          serviceDate,
          serviceType: (alert as any).alertType || "SCHEDULED_SERVICE",
          serviceId,
          status: "SCHEDULED",
          dealerId: selectedProvider.value || "tbd",
          serviceDetails: {
            providerLabel: selectedProvider.label,
            serviceMode: serviceType,
            notes: notes || "",
            alertType: (alert as any).alertType,
            alertSeverity: (alert as any).severity,
            alertDescription: (alert as any).description || (alert as any).message || "",
            triggerSource: "operator-ui-schedule",
          },
          cost: {
            estimatedAmount: (alert as any).estimatedCost || 0,
          },
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        const res = await fetch(`${apiEndpoint}api/v1/service-history`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...getAuthHeaders(),
          },
          body: JSON.stringify({ entry: record }),
        });
        if (!res.ok) {
          const errBody = await res.text();
          throw new Error(`POST service-history: HTTP ${res.status}: ${errBody.slice(0, 200)}`);
        }
      }

      // Flip the maintenance-alert status to SCHEDULED so the Service tab
      // shows these as "in progress" rather than "open". Best-effort — if
      // this fails the service-history rows still exist and the demo still
      // works, just with a slightly inconsistent Service tab status.
      for (const alert of alertsForRecord) {
        const alertId = (alert as any).alertId;
        if (!alertId) continue;  // ad-hoc scheduling has no alertId
        try {
          await fetch(`${apiEndpoint}api/v1/maintenance-alerts/${alertId}`, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              ...getAuthHeaders(),
            },
            body: JSON.stringify({ status: "SCHEDULED" }),
          });
        } catch { /* non-critical */ }
      }

      setSubmitted(true);
      setTimeout(() => {
        setSubmitted(false);
        setSelectedProvider(null);
        setSelectedDate("");
        setNotes("");
        onDismiss();
      }, 2000);
    } catch (e: any) {
      setError(e?.message || "Failed to schedule service");
    } finally {
      setSubmitting(false);
    }
  };

  const filteredProviders = serviceProviders.filter(p =>
    serviceType === "mobile" ? p.type === "Mobile" :
    serviceType === "dealer" ? p.type === "Dealer" :
    p.type === "Service Center"
  );

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={<Header variant="h2">Schedule Service</Header>}
      size="large"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss} disabled={submitting}>Cancel</Button>
            <Button variant="primary" onClick={handleSubmit}
                    disabled={!selectedProvider || !selectedDate || submitted || submitting}
                    loading={submitting}>
              {submitted ? "Scheduled ✓" : (submitting ? "Scheduling..." : "Schedule Service")}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      {submitted ? (
        <Box textAlign="center" padding="xl">
          <StatusIndicator type="success">
            Service scheduled successfully for {vehicleId} ({vin})
          </StatusIndicator>
          <Box variant="p" color="text-body-secondary" margin={{ top: "s" }}>
            Confirmation sent to service provider. Work order created.
          </Box>
        </Box>
      ) : (
        <SpaceBetween size="l">
          {error && <Alert type="error" dismissible onDismiss={() => setError(null)}>{error}</Alert>}
          {/* Vehicle & Alert Summary */}
          <Container header={<Header variant="h3">Service Items ({selectedAlerts.length})</Header>}>
            <SpaceBetween size="xs">
              <Box variant="small" color="text-body-secondary">
                Vehicle: {vehicleId} — {vin}
              </Box>
              {selectedAlerts.map((alert, i) => (
                <Box key={i}>
                  <StatusIndicator type={alert.severity === 'CRITICAL' || alert.severity === 'HIGH' ? 'error' : 'warning'}>
                    {(alert.alertType || '').replace(/_/g, ' ')} — {alert.description || alert.message}
                    {alert.estimatedCost ? ` (Est. $${alert.estimatedCost})` : ''}
                  </StatusIndicator>
                </Box>
              ))}
              {totalEstCost > 0 && (
                <Box variant="strong" margin={{ top: "xs" }}>Total estimated cost: ${totalEstCost.toLocaleString()}</Box>
              )}
            </SpaceBetween>
          </Container>

          {/* Service Type */}
          <FormField label="Service Type">
            <RadioGroup
              value={serviceType}
              onChange={({ detail }) => { setServiceType(detail.value); setSelectedProvider(null); }}
              items={[
                { value: "service-center", label: "Service Center", description: "Drop off at a nearby service center" },
                { value: "mobile", label: "Mobile Service", description: "Technician comes to the vehicle location" },
                { value: "dealer", label: "Dealer / OEM", description: "Schedule at an authorized dealer" },
              ]}
            />
          </FormField>

          {/* Provider Selection */}
          <FormField label="Service Provider">
            <Select
              selectedOption={selectedProvider}
              onChange={({ detail }) => setSelectedProvider(detail.selectedOption)}
              placeholder="Select a service provider"
              options={filteredProviders.map(p => ({
                value: p.value,
                label: p.label,
                description: `${p.distance} · Next available: ${p.nextAvailable}`,
              }))}
            />
          </FormField>

          {/* Date */}
          <FormField label="Preferred Date">
            <DatePicker
              value={selectedDate}
              onChange={({ detail }) => setSelectedDate(detail.value)}
              placeholder="YYYY/MM/DD"
            />
          </FormField>

          {/* Notes */}
          <FormField label="Notes (optional)">
            <Textarea
              value={notes}
              onChange={({ detail }) => setNotes(detail.value)}
              placeholder="Additional instructions for the service provider..."
              rows={3}
            />
          </FormField>
        </SpaceBetween>
      )}
    </Modal>
  );
};

export default ScheduleServiceModal;
