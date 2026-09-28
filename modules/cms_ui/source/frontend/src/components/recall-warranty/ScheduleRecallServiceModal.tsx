// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  DatePicker,
  Form,
  FormField,
  Link,
  Modal,
  Multiselect,
  Select,
  SpaceBetween,
  StatusIndicator,
  Textarea,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import {
  dealerPlaceholder,
  useDealerOptions,
} from "../../hooks/useDealerOptions";

// Service centres are fetched, never embedded.
//
// This file used to export `SERVICE_CENTER_OPTIONS`, six invented service
// centres, which spec 2026-09-02-cms-fleet-intelligence-v1 T5.3 emptied to `[]`
// — correct as far as it went, but it left both this picker and
// ServiceDashboard's offering zero options behind a form that still looked
// functional. T4.2 of 2026-09-02-cms-dms-service-convergence replaces the array
// with `useDealerOptions()`, which reads CMS's `/api/v1/dealers` — a server-side
// read-through to DMS's authorised rooftops (spec D3/D6).
//
// The export is GONE, deliberately. Re-adding one would give a future
// contributor somewhere to put a literal list again; there is now no such place
// in this module.

interface VinOption {
  label: string;
  value: string;
  description?: string;
}

interface ScheduleRecallServiceModalProps {
  /** Whether the modal is open. Owned by the consumer page. */
  visible: boolean;
  /** The recall whose service is being scheduled. Modal does nothing useful if null. */
  recall: any | null;
  /** Called whenever the modal should close (cancel button, post-success auto-close, etc.). */
  onDismiss: () => void;
  /**
   * Optional pre-fetched vehicleId -> VIN map. If omitted, the modal fetches
   * its own copy on first visible. Pass when the parent already has the map
   * (e.g. ServiceDashboard uses it for several other features) so we don't
   * fire the same /api/v1/vehicles request twice.
   */
  vehicleVinMap?: Record<string, string>;
}

/**
 * Schedule Recall Service modal.
 *
 * Used from any page that surfaces an active recall and wants the operator
 * to be able to dispatch work for some-or-all of the affected VINs in a
 * single action. Submit fans out to POST /api/v1/service-history with one
 * row per selected VIN; status is lowercase 'scheduled' (matches book.py /
 * _approve_dtc_action_followups convention so each row appears under the
 * Scheduled filter on the corresponding vehicle's Service tab — see
 * main_api/index.py L380-L389).
 *
 * Self-contained: owns its form state, submit logic, optional vehicle-VIN
 * fetch, and the partial-success Alert. Consumer only owns visible/recall
 * and the dismiss callback.
 */
export const ScheduleRecallServiceModal: React.FC<ScheduleRecallServiceModalProps> = ({
  visible,
  recall,
  onDismiss,
  vehicleVinMap: externalMap,
}) => {
  const [internalMap, setInternalMap] = useState<Record<string, string>>({});
  const vehicleVinMap = externalMap ?? internalMap;
  // Fetch only while the modal is open — see the hook's `enabled` note.
  const {
    options: dealerOptions,
    status: dealerStatus,
    errorMessage: dealerError,
    reload: reloadDealers,
  } = useDealerOptions(visible);

  const [selectedVins, setSelectedVins] = useState<VinOption[]>([]);
  const [dealer, setDealer] = useState<{ label: string; value: string } | null>(null);
  const [serviceDate, setServiceDate] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: number; fail: number } | null>(null);

  const buildOptions = (vehicleIds: string[], map: Record<string, string>): VinOption[] =>
    vehicleIds.map((vid) => {
      const vin = map[vid];
      return {
        label: vin || vid,
        value: vid,
        // Deliberately does NOT echo `vid` when the VIN resolves. vehicleIds are
        // internal DDB keys and some carry legacy brand names (see
        // issues/2026-09-20-unrebranded-vins-on-meridian-vehicles/); the VIN in
        // the label is the operator-facing identifier.
        description: vin ? undefined : "VIN not found in fleet — may be a population match",
      };
    });

  // Fetch the vehicleId -> VIN map only when the parent didn't already
  // provide one and the modal becomes visible. We only fetch once per
  // mount (cached in internalMap state); subsequent opens reuse it.
  useEffect(() => {
    if (externalMap) return;
    if (!visible) return;
    if (Object.keys(internalMap).length > 0) return;
    let cancelled = false;
    (async () => {
      try {
        const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
        const resp = await authFetch(`${apiEndpoint}/api/v1/vehicles?limit=500`);
        if (!resp.ok) return;
        const data = await resp.json();
        const next: Record<string, string> = {};
        for (const v of data.vehicles || []) {
          if (v.vehicleId && v.vin) next[v.vehicleId] = v.vin;
        }
        if (!cancelled) setInternalMap(next);
      } catch (e) {
        // Non-fatal — Multiselect falls back to vehicleId in option labels
        // when the map is empty or missing an entry.
        // eslint-disable-next-line no-console
        console.warn("ScheduleRecallServiceModal: vehicle VIN fetch failed:", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [visible, externalMap, internalMap]);

  // Reset form whenever the recall context changes — e.g. operator opens
  // the modal for recall A, cancels, opens it for recall B; we don't want
  // the previous selection/dealer/date/notes to leak across.
  useEffect(() => {
    if (recall) {
      setSelectedVins(buildOptions(recall.vehicles || [], vehicleVinMap));
      setDealer(null);
      setServiceDate("");
      setNotes("");
      setResult(null);
    }
    // We deliberately rely on `recall` identity here; vehicleVinMap may
    // arrive after this effect runs, but the Multiselect rebuilds its
    // option labels each render against the current map so the late
    // arrival won't strand the operator on vehicleId-only labels.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recall]);

  // When the VIN map arrives after the recall was set, refresh the
  // selected options so labels flip from vehicleId to VIN without making
  // the operator reopen the modal.
  useEffect(() => {
    if (!recall) return;
    setSelectedVins((prev) =>
      prev.map((opt) => {
        const vin = vehicleVinMap[opt.value];
        if (!vin) return opt;
        return {
          ...opt,
          label: vin,
          description: opt.value,
        };
      })
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleVinMap]);

  const submit = async () => {
    if (!recall || !dealer || !serviceDate || selectedVins.length === 0) return;
    setSubmitting(true);
    setResult(null);
    let ok = 0;
    let fail = 0;
    const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
    const recallSummary = String(recall.component || "").split(":").slice(0, 2).join(": ");
    for (const opt of selectedVins) {
      const vehicleId = opt.value;
      const vin = vehicleVinMap[vehicleId] || opt.label;
      const body = {
        vehicleId,
        vin,
        serviceDate,
        serviceType: "RECALL_SERVICE",
        dealerId: dealer.value,
        provider: dealer.label,
        // Lowercase 'scheduled' to match the convention used by the voice
        // agent's book() tool and _approve_dtc_action_followups (see
        // main_api/index.py L380-L389) so each row appears under the
        // Scheduled filter on the corresponding vehicle's Service tab.
        status: "scheduled",
        category: "RECALL",
        description: `Recall ${recall.id}: ${recallSummary}`,
        notes:
          notes ||
          `Scheduled from CMS UI Recalls. NHTSA #${recall.id}.`,
        serviceDetails: {
          recallId: recall.id,
          recallComponent: recall.component,
          recallSeverity: recall.severity,
          source: "cms-ui-recalls",
        },
      };
      try {
        const resp = await authFetch(`${apiEndpoint}/api/v1/service-history`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (resp.ok) ok++;
        else fail++;
      } catch {
        fail++;
      }
    }
    setSubmitting(false);
    setResult({ ok, fail });
    if (fail === 0) {
      // brief success-glance before auto-closing so the operator sees
      // the confirmation; if any failed we leave the modal open so they
      // can retry the failures.
      setTimeout(() => onDismiss(), 1300);
    }
  };

  const optionsForCurrentRecall = recall ? buildOptions(recall.vehicles || [], vehicleVinMap) : [];

  return (
    <Modal
      visible={visible}
      onDismiss={() => !submitting && onDismiss()}
      size="large"
      header={recall ? `Schedule Recall Service — ${recall.id}` : "Schedule Recall Service"}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss} disabled={submitting}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={submit}
              loading={submitting}
              disabled={!dealer || !serviceDate || selectedVins.length === 0 || submitting}
            >
              {submitting
                ? "Scheduling…"
                : `Schedule ${selectedVins.length} ${selectedVins.length === 1 ? "vehicle" : "vehicles"}`}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      {recall && (
        <Form>
          <SpaceBetween size="m">
            <Container>
              <SpaceBetween size="xs">
                <Box variant="awsui-key-label">Recall</Box>
                <Box>
                  <Link href={`https://www.nhtsa.gov/recalls?nhtsaId=${recall.id}`} external>
                    NHTSA #{recall.id}
                  </Link>
                  {" — "}
                  <StatusIndicator
                    type={
                      recall.severity === "Critical" ? "error" : recall.severity === "High" ? "warning" : "info"
                    }
                  >
                    {recall.severity}
                  </StatusIndicator>
                </Box>
                <Box color="text-body-secondary" fontSize="body-s">{recall.component}</Box>
              </SpaceBetween>
            </Container>

            <FormField
              label={`Vehicles (${selectedVins.length} of ${(recall.vehicles || []).length} selected)`}
              description="All affected VINs are pre-selected. Deselect any you don't want to dispatch in this batch — one service-history row will be created per VIN."
            >
              <Multiselect
                selectedOptions={selectedVins}
                onChange={({ detail }) => setSelectedVins(detail.selectedOptions as any)}
                options={optionsForCurrentRecall}
                placeholder="Select VINs to schedule"
                tokenLimit={4}
                filteringType="auto"
                empty="No vehicles affected"
              />
            </FormField>

            <FormField
              label="Service center"
              description="Where the recall work will be performed. Books the appointment under this provider name on each vehicle's service history."
              errorText={dealerStatus === "error" ? dealerError : undefined}
            >
              <SpaceBetween size="xs">
                <Select
                  selectedOption={dealer as any}
                  onChange={({ detail }) => setDealer(detail.selectedOption as any)}
                  options={dealerOptions}
                  placeholder={dealerPlaceholder(dealerStatus)}
                  loadingText="Loading service centres…"
                  statusType={dealerStatus === "loading" ? "loading" : "finished"}
                  disabled={dealerStatus !== "ready"}
                  empty="No authorised service centres available"
                  triggerVariant="option"
                />
                {/* A retry, not a fallback. The operator can re-ask; the form
                    never invents a roster to keep itself usable. */}
                {dealerStatus === "error" && (
                  <Button onClick={reloadDealers} disabled={submitting}>
                    Retry loading service centres
                  </Button>
                )}
                {dealerStatus === "empty" && (
                  <Box color="text-status-inactive" fontSize="body-s">
                    The dealer directory returned no authorised rooftops. Recall work
                    can only be booked at an authorised service centre.
                  </Box>
                )}
              </SpaceBetween>
            </FormField>

            <FormField label="Service date" description="Operator-confirmed appointment date.">
              <DatePicker
                value={serviceDate}
                onChange={({ detail }) => setServiceDate(detail.value)}
                placeholder="YYYY-MM-DD"
              />
            </FormField>

            <FormField
              label="Notes"
              description="Optional. Anything the dispatcher or service center should know — preferred drop-off time, contact, special instructions."
            >
              <Textarea
                value={notes}
                onChange={({ detail }) => setNotes(detail.value)}
                placeholder="e.g. Drop trucks Monday 7am, contact dispatcher on arrival…"
                rows={3}
              />
            </FormField>

            {result && (
              <Alert
                type={result.fail === 0 ? "success" : result.ok === 0 ? "error" : "warning"}
                header={
                  result.fail === 0
                    ? `Scheduled ${result.ok} ${result.ok === 1 ? "vehicle" : "vehicles"}`
                    : result.ok === 0
                    ? `Failed to schedule ${result.fail} ${result.fail === 1 ? "vehicle" : "vehicles"}`
                    : `Partial success — ${result.ok} scheduled, ${result.fail} failed`
                }
              >
                {result.fail > 0
                  ? "The failed rows can be retried by submitting again — already-scheduled VINs will be created as additional appointments, so deselect them first."
                  : "All vehicles will appear under the Scheduled filter on each vehicle's Service tab."}
              </Alert>
            )}
          </SpaceBetween>
        </Form>
      )}
    </Modal>
  );
};

export default ScheduleRecallServiceModal;
