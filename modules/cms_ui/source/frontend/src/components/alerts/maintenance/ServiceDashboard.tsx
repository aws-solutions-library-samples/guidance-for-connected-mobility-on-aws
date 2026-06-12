// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from "react";
import {
  Box,
  Button,
  CollectionPreferences,
  ColumnLayout,
  Container,
  DatePicker,
  Form,
  FormField,
  Grid,
  Header,
  Link,
  Modal,
  Pagination,
  Popover,
  ProgressBar,
  Select,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
  Textarea,
  TextFilter,
  Alert,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../../config/api";
import { authFetch } from "../../../utils/authFetch";
import { nhtsaRecalls } from "../../recall-warranty/nhtsaRecallData";
import RecallAgentFeed from "../../recall-warranty/RecallAgentFeed";
import ScheduleRecallServiceModal, { SERVICE_CENTER_OPTIONS } from "../../recall-warranty/ScheduleRecallServiceModal";

interface MaintenanceAlert {
  alertId: string;
  vehicleId: string;
  vin: string;
  alertType: string;
  category: string;
  severity: string;
  status: string;
  message: string;
  estimatedCost: number;
  estimatedDuration: number;
  triggerField: string;
  triggerCondition: string;
  currentValue: number;
  thresholdValue: number;
  trendDirection: string;
  priority: number;
  createdDate: number;
  dueDate: number;
  // Rich diagnostic fields used by the Alert Details modal — surfaced
  // by the Flink MaintenanceProcessor for each alert and operationally
  // useful for the technician/dispatcher reading the row.
  repairInstructions: string;
  requiredTools: string;
  manualReference: string;
  safetyWarnings: string;
  daysOpen: number;
  escalationLevel: number;
  // Tag the source so cells/details-modal can render appropriately.
  // 'ALERT'   = telemetry-triggered row from cms-prod-storage-maintenance-alerts.
  // 'SERVICE' = scheduled or completed work from cms-prod-storage-service-history.
  recordType: 'ALERT' | 'SERVICE';
  // Dealer/service-center name — only populated for SERVICE rows.
  provider?: string;
}

// Short allowlist of automotive acronyms preserved as caps when title-casing
// alert types. Mirrors the list in VehicleDetailView so 'AC_COMPRESSOR'
// reads as 'AC Compressor' rather than 'Ac Compressor', without falsely
// preserving common 3-letter words like OIL/PAD/GAS that happen to be
// uppercase in the source enum.
const TYPE_ACRONYMS = new Set([
  "VSA",
  "DEF",
  "AC",
  "DTC",
  "ABS",
  "ECM",
  "RPM",
  "EGR",
  "API",
  "GPS",
]);

// Strip the leading dotted namespace (e.g. 'maintenance.coolant_critical_overheat'
// → 'coolant_critical_overheat'), swap underscores for spaces, then
// title-case each token while preserving known acronyms. Returns 'Unknown'
// for empty/null input so the cell never shows blank.
const cleanAlertType = (raw: string | undefined | null): string => {
  const stripped = String(raw || "Unknown")
    .replace(/^[^.]+\./, "")
    .replace(/_/g, " ");
  return stripped
    .split(/\s+/)
    .filter(Boolean)
    .map((word) =>
      TYPE_ACRONYMS.has(word.toUpperCase())
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
    )
    .join(" ");
};

// Older alerts pack '<message> — <advisory> — <metric>' into a single
// long string that's noisy in a table cell. We surface only the head
// segment (everything before the first em-dash) and ellipsis-clip it,
// then expose the full text via a Popover hover. Newer single-clause
// descriptions pass through unchanged.
const summarizeAlertMessage = (raw: string | undefined | null): { head: string; full: string; truncated: boolean } => {
  const full = String(raw || "");
  const head = full.split(/\s+—\s+|\s+--\s+/)[0];
  const MAX = 70;
  const clipped = head.length > MAX ? head.slice(0, MAX).trimEnd() + "…" : head;
  return { head: clipped, full, truncated: clipped !== full };
};

const ServiceDashboard: React.FC = () => {
  const [alerts, setAlerts] = useState<MaintenanceAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // Status filter for the Maintenance Alerts table. Defaults to ACTIVE
  // (open + scheduled + in-progress) — what an operator wants to triage
  // on landing — but the dropdown also exposes Open Only, Scheduled
  // Only, Completed, and All Statuses so historical context is one
  // click away. Mirrors the equivalent filter on the vehicle detail
  // Service tab so operators get the same mental model in both places.
  const [alertStatusFilter, setAlertStatusFilter] = useState("ACTIVE");

  // vehicleId -> VIN map. nhtsaRecalls only carries vehicleIds (VEH-####)
  // and operators want to see the 17-char VIN they know their truck by;
  // we resolve once on mount via the existing list endpoint and look up
  // synchronously from then on.
  const [vehicleVinMap, setVehicleVinMap] = useState<Record<string, string>>({});

  // Schedule Recall Service modal state. Each recall affects N vehicles;
  // operators want to dispatch one batch of work to one service center
  // for some-or-all of those VINs in a single action. The form/submit
  // logic lives in <ScheduleRecallServiceModal /> so the same modal can
  // be rendered from /recalls without copying the body. We only own
  // visibility + which recall is currently being scheduled here.
  const [scheduleModalVisible, setScheduleModalVisible] = useState(false);
  const [scheduleRecall, setScheduleRecall] = useState<any>(null);

  // Alert Details modal — opened by clicking the Type cell. Read-only
  // view of every operationally-useful field on the alert (full
  // message, trigger condition, repair instructions, required tools,
  // safety warnings) plus a Predictive Insights placeholder section
  // for upcoming tire-wear / brake-degradation models. Footer chains
  // into the existing alert-schedule modal so an operator can read
  // the details and dispatch service in one motion.
  const [detailsModalVisible, setDetailsModalVisible] = useState(false);
  const [detailsAlert, setDetailsAlert] = useState<MaintenanceAlert | null>(null);

  const openAlertDetailsModal = (alert: MaintenanceAlert) => {
    setDetailsAlert(alert);
    setDetailsModalVisible(true);
  };

  // Schedule Service modal state for an individual maintenance alert. The
  // calendar icon on each Open Maintenance Alerts row used to be a stub
  // with no onClick — operators wanted it to dispatch service for the
  // alert's specific vehicle without leaving the page. Single-VIN flow,
  // so we don't need the Multiselect from the recalls modal.
  const [alertScheduleVisible, setAlertScheduleVisible] = useState(false);
  const [alertScheduleAlert, setAlertScheduleAlert] = useState<MaintenanceAlert | null>(null);
  const [alertScheduleDealer, setAlertScheduleDealer] = useState<{ label: string; value: string } | null>(null);
  const [alertScheduleDate, setAlertScheduleDate] = useState("");
  const [alertScheduleNotes, setAlertScheduleNotes] = useState("");
  const [alertScheduleSubmitting, setAlertScheduleSubmitting] = useState(false);
  const [alertScheduleError, setAlertScheduleError] = useState<string | null>(null);

  useEffect(() => {
    fetchAlerts();
    fetchVehicleVinMap();
  }, []);

  // Fetch the fleet's vehicleId -> VIN mapping once so the recalls
  // multi-select shows real VINs instead of internal slugs. Failures here
  // are non-fatal — the modal falls back to vehicleId in the option label
  // if the map is empty or missing an entry.
  const fetchVehicleVinMap = async () => {
    try {
      const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
      const resp = await authFetch(`${apiEndpoint}/api/v1/vehicles?limit=500`);
      if (!resp.ok) return;
      const data = await resp.json();
      const next: Record<string, string> = {};
      for (const v of data.vehicles || []) {
        if (v.vehicleId && v.vin) next[v.vehicleId] = v.vin;
      }
      setVehicleVinMap(next);
    } catch (e) {
      console.warn("Failed to load vehicle VIN map:", e);
    }
  };

  // Build Multiselect options from a list of vehicleIds. VIN as label
  // (operator-facing), vehicleId as the value we send to the API and
  // also surface as the description so the operator can correlate.
  // (Still used by the Recalls table cell's Fleet Vehicles popover; the
  // shared ScheduleRecallServiceModal builds its own options internally
  // from the same map.)
  const vehicleIdsToOptions = (vehicleIds: string[]) =>
    vehicleIds.map((vid) => {
      const vin = vehicleVinMap[vid];
      return {
        label: vin || vid,
        value: vid,
        description: vin ? vid : "VIN not found in fleet — may be a population match",
      };
    });

  const openScheduleModal = (recall: any) => {
    setScheduleRecall(recall);
    setScheduleModalVisible(true);
  };

  const openScheduleAlertModal = (alert: MaintenanceAlert) => {
    setAlertScheduleAlert(alert);
    setAlertScheduleDealer(null);
    setAlertScheduleDate("");
    setAlertScheduleNotes("");
    setAlertScheduleError(null);
    setAlertScheduleVisible(true);
  };

  // Submit a single service-history row for the maintenance alert that
  // was clicked. Same backend call as the recalls flow but single-VIN, so
  // there's no partial-success bookkeeping — either the row gets created
  // or we surface the error inline. On success we close the modal and
  // refresh the alerts list so the operator sees the alert flip into a
  // 'has scheduled service' state if/when the API ties them together.
  const submitAlertSchedule = async () => {
    if (!alertScheduleAlert || !alertScheduleDealer || !alertScheduleDate) return;
    setAlertScheduleSubmitting(true);
    setAlertScheduleError(null);
    try {
      const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
      const a = alertScheduleAlert;
      const cleanedType = String(a.alertType || "")
        .replace(/^[^.]+\./, "")
        .replace(/_/g, " ")
        .trim();
      const headMessage = String(a.message || "").split(/\s+—\s+|\s+--\s+/)[0];
      const body = {
        vehicleId: a.vehicleId,
        vin: a.vin || vehicleVinMap[a.vehicleId] || "",
        serviceDate: alertScheduleDate,
        serviceType: "MAINTENANCE_SERVICE",
        dealerId: alertScheduleDealer.value,
        provider: alertScheduleDealer.label,
        // Lowercase 'scheduled' to match the convention used by the
        // voice agent's book() tool and _approve_dtc_action_followups
        // (see main_api/index.py L380-L389) so the row appears under
        // the Scheduled filter on the vehicle detail Service tab.
        status: "scheduled",
        category: "MAINTENANCE_ALERT",
        description: cleanedType ? `${cleanedType}: ${headMessage}`.slice(0, 500) : headMessage.slice(0, 500),
        notes: alertScheduleNotes || `Scheduled from /alerts/maintenance for alert ${a.alertId}.`,
        serviceDetails: {
          alertId: a.alertId,
          alertType: a.alertType,
          severity: a.severity,
          triggerCondition: a.triggerCondition || "",
          source: "cms-ui-maintenance-alerts-tab",
        },
      };
      const resp = await authFetch(`${apiEndpoint}/api/v1/service-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => `${resp.status}`);
        throw new Error(`Failed to schedule (${resp.status}): ${text.slice(0, 200)}`);
      }
      setAlertScheduleVisible(false);
    } catch (e: any) {
      setAlertScheduleError(e?.message || "Failed to schedule service");
    } finally {
      setAlertScheduleSubmitting(false);
    }
  };

  const fetchAlerts = async () => {
    try {
      const apiEndpoint = getApiEndpoint().replace(/\/$/, '');

      // Two-source merge so the Maintenance tab spans the full lifecycle:
      //   - /api/v1/maintenance-alerts: telemetry-triggered alerts (OPEN /
      //     IN_PROGRESS / RESOLVED) from the Flink processor.
      //   - /api/v1/service-history: actual service work scheduled or
      //     completed (SCHEDULED / COMPLETED / RESOLVED).
      // Operators want one place to see "what's wrong" and "what was done"
      // about it; merging client-side keeps the backend untouched. Calls
      // run in parallel — partial-success path: if one of the two fails,
      // we still render whatever the other returned.
      const [alertsResp, historyResp] = await Promise.allSettled([
        authFetch(`${apiEndpoint}/api/v1/maintenance-alerts?limit=500`),
        authFetch(`${apiEndpoint}/api/v1/service-history?limit=500`),
      ]);

      const collected: MaintenanceAlert[] = [];

      // ── Alerts ───────────────────────────────────────────────────────
      if (alertsResp.status === "fulfilled" && alertsResp.value.ok) {
        const data = await alertsResp.value.json();
        const alertList = data.alerts || [];
        console.log(`✅ Service dashboard loaded ${alertList.length} maintenance alerts`);
        for (const a of alertList) {
          collected.push({
            alertId: a.alertId || a.id,
            vehicleId: a.vehicleId,
            // VIN is the operator-facing identifier — VEH-#### is internal.
            // Older alerts in DDB don't have a vin field; we fall back to
            // vehicleId in the cell renderer rather than failing.
            vin: a.vin || '',
            alertType: a.alertType || a.type,
            category: a.category,
            severity: a.severity,
            // Status is normalised to uppercase so the new status filter
            // dropdown below treats lowercase 'scheduled' rows (the voice
            // agent's book() tool and _approve_dtc_action_followups write
            // lowercase — see main_api/index.py L380-L389) the same as
            // historical uppercase 'SCHEDULED' / 'OPEN' / 'COMPLETED' rows.
            // Without this, the filter would silently exclude voice-agent
            // bookings the same way the vehicle detail Service tab did
            // before its corresponding fix.
            status: (a.status ? String(a.status).toUpperCase() : (a.resolved ? 'RESOLVED' : 'OPEN')),
            message: a.message || a.description,
            estimatedCost: a.estimatedCost || 0,
            estimatedDuration: a.estimatedDuration || 0,
            triggerField: a.triggerField || '',
            triggerCondition: a.triggerCondition || '',
            currentValue: a.currentValue || 0,
            thresholdValue: a.thresholdValue || 0,
            trendDirection: a.trendDirection || '',
            priority: a.priority || (a.severity === 'CRITICAL' ? 1 : a.severity === 'HIGH' ? 2 : a.severity === 'MEDIUM' ? 3 : 4),
            createdDate: a.createdDate || a.timestamp || 0,
            dueDate: a.dueDate || 0,
            repairInstructions: a.repairInstructions || '',
            requiredTools: a.requiredTools || '',
            manualReference: a.manualReference || '',
            safetyWarnings: a.safetyWarnings || '',
            daysOpen: a.daysOpen || 0,
            escalationLevel: a.escalationLevel || 0,
            recordType: 'ALERT',
          });
        }
      } else {
        const reason = alertsResp.status === "fulfilled"
          ? `${alertsResp.value.status}`
          : (alertsResp.reason as Error)?.message;
        console.error(`Failed to fetch maintenance alerts: ${reason}`);
      }

      // ── Service history (scheduled + completed work) ────────────────
      // service_history rows don't carry severity/trend/triggerCondition
      // (they're not telemetry-triggered) so we surface those fields with
      // sensible empty defaults. Severity is derived from any nested
      // serviceDetails the recall/alert-schedule modal stamped on the
      // row when it was booked, which preserves the original alert
      // severity through to the merged view.
      if (historyResp.status === "fulfilled" && historyResp.value.ok) {
        const hdata = await historyResp.value.json();
        const records = hdata.serviceRecords || [];
        console.log(`✅ Service dashboard loaded ${records.length} service-history records`);
        for (const r of records) {
          const detailsBag = r.serviceDetails || {};
          const inheritedSeverity =
            (detailsBag.recallSeverity || detailsBag.severity || '').toString().toUpperCase();
          // serviceDate is sometimes a YYYY-MM-DD or full ISO string —
          // turn into a millisecond timestamp so the createdDate-based
          // sort below works on both sources.
          let createdMs = 0;
          const sd = r.serviceDate || r.createdAt;
          if (typeof sd === 'string' && sd) {
            const parsed = Date.parse(sd);
            if (!Number.isNaN(parsed)) createdMs = parsed;
          } else if (typeof sd === 'number') {
            createdMs = sd > 9_999_999_999 ? sd : sd * 1000;
          }
          collected.push({
            // serviceId is unique to service-history rows; we namespace
            // it so it can't collide with an alertId in the merged list.
            alertId: r.serviceId ? `svc-${r.serviceId}` : (r.alertId || `svc-${createdMs}`),
            vehicleId: r.vehicleId,
            vin: r.vin || '',
            alertType: r.serviceType || 'SERVICE',
            category: r.category || 'SERVICE',
            severity: inheritedSeverity || '',
            status: (r.status ? String(r.status).toUpperCase() : 'COMPLETED'),
            message: r.description || r.notes || '',
            estimatedCost: r.cost?.totalCost || r.estimatedCost || 0,
            estimatedDuration: r.estimatedDuration || 0,
            triggerField: '',
            triggerCondition: '',
            currentValue: 0,
            thresholdValue: 0,
            trendDirection: '',
            priority: (() => {
              const sev = inheritedSeverity || '';
              if (sev === 'CRITICAL') return 1;
              if (sev === 'HIGH') return 2;
              if (sev === 'MEDIUM') return 3;
              return 5; // service rows sink below alerts (which are 1-4)
            })(),
            createdDate: createdMs,
            dueDate: 0,
            repairInstructions: r.notes || '',
            requiredTools: '',
            manualReference: detailsBag.recallId
              ? `NHTSA Recall #${detailsBag.recallId}`
              : (detailsBag.alertId ? `From alert ${detailsBag.alertId}` : ''),
            safetyWarnings: '',
            daysOpen: 0,
            escalationLevel: 0,
            recordType: 'SERVICE',
            // Provider name is set by the schedule modal (e.g. 'Rush
            // Truck Center — Dallas') — surface it in the details modal
            // so the operator can see who's doing the work without
            // round-tripping back to the schedule view.
            provider: r.provider || r.dealerId || '',
          } as MaintenanceAlert);
        }
      } else {
        const reason = historyResp.status === "fulfilled"
          ? `${historyResp.value.status}`
          : (historyResp.reason as Error)?.message;
        console.error(`Failed to fetch service history: ${reason}`);
      }

      setAlerts(collected);
    } finally {
      setLoading(false);
    }
  };

  // KPI cards always show Open-only counts — they're the "what
  // requires action right now" headlines, not a reflection of the
  // table's current view. Keeping these decoupled from the table
  // filter means switching the table to "Completed" doesn't make the
  // big "Open Alerts" card go to zero.
  const openAlerts = alerts.filter(a => a.status === "OPEN");
  const criticalAlerts = openAlerts.filter(a => a.severity === "CRITICAL");
  const totalEstCost = openAlerts.reduce((s, a) => s + (a.estimatedCost || 0), 0);
  const totalRecalls = nhtsaRecalls.length;
  const totalRecallVehicles = nhtsaRecalls.reduce((s, r) => s + r.affected, 0);

  // Step 1 of table filtering — apply the status dropdown. ACTIVE is
  // the operator's default landing view: anything that still needs
  // action (no status / OPEN / IN_PROGRESS / SCHEDULED). The other
  // options narrow further, ALL surfaces every record. See the Select
  // below for labels.
  const statusFilteredAlerts = alerts.filter(a => {
    if (alertStatusFilter === "ALL") return true;
    if (alertStatusFilter === "ACTIVE") return !a.status || a.status === "OPEN" || a.status === "IN_PROGRESS" || a.status === "SCHEDULED";
    if (alertStatusFilter === "OPEN") return !a.status || a.status === "OPEN";
    if (alertStatusFilter === "IN_PROGRESS") return a.status === "IN_PROGRESS" || a.status === "SCHEDULED";
    if (alertStatusFilter === "COMPLETED") return a.status === "COMPLETED" || a.status === "RESOLVED";
    return true;
  });

  // Step 2 — apply the free-text filter on top of the status filter.
  // Same fields as before plus VIN-via-map fallback for older rows
  // whose own .vin is empty.
  const filteredAlerts = statusFilteredAlerts.filter(a =>
    !filterText ||
    a.vehicleId?.toLowerCase().includes(filterText.toLowerCase()) ||
    a.vin?.toLowerCase().includes(filterText.toLowerCase()) ||
    (vehicleVinMap[a.vehicleId] || "").toLowerCase().includes(filterText.toLowerCase()) ||
    a.alertType?.toLowerCase().includes(filterText.toLowerCase()) ||
    a.message?.toLowerCase().includes(filterText.toLowerCase())
  );

  // Filter-aware header label so the table's Header counter line tells
  // the operator which slice of the data they're looking at.
  // Filter-aware header label. The tab now spans both telemetry alerts
  // and the service-history rows that resolve them, so the headings drop
  // 'Alerts' from the heading and read in plain operator language.
  const filterLabel =
    alertStatusFilter === "ALL" ? "All Maintenance Records" :
    alertStatusFilter === "OPEN" ? "Open Alerts" :
    alertStatusFilter === "IN_PROGRESS" ? "Scheduled Service" :
    alertStatusFilter === "COMPLETED" ? "Completed Service" :
    "Active Maintenance";

  return (
    <SpaceBetween size="l">
      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Open Alerts</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{loading ? "..." : openAlerts.length}</span>
            <StatusIndicator type={criticalAlerts.length > 0 ? "error" : "success"}>
              {criticalAlerts.length} critical
            </StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Est. Service Cost</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>${totalEstCost.toLocaleString()}</span>
            <Box color="text-body-secondary" fontSize="body-s">Open alerts total</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Active Recalls</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalRecalls}</span>
            <StatusIndicator type="warning">{totalRecallVehicles} vehicles affected</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Safety Critical</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: criticalAlerts.length > 0 ? '#d91515' : undefined }}>{criticalAlerts.length}</span>
            <StatusIndicator type={criticalAlerts.length > 0 ? "error" : "success"}>
              {criticalAlerts.length > 0 ? "Immediate action" : "All clear"}
            </StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Total Alerts</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{loading ? "..." : alerts.length}</span>
            <Box color="text-body-secondary" fontSize="body-s">All statuses</Box>
          </SpaceBetween>
        </Container>
      </div>

      {/* Critical alert banner */}
      {criticalAlerts.length > 0 && (
        <Alert type="error" header={`${criticalAlerts.length} Critical Safety Alerts — Immediate Action Required`}>
          {criticalAlerts.slice(0, 3).map(a => (
            <div key={a.alertId}>• {a.vin || vehicleVinMap[a.vehicleId] || a.vehicleId}: {summarizeAlertMessage(a.message).head}</div>
          ))}
        </Alert>
      )}

      {/* Tabs */}
      <Container>
      <Tabs
        tabs={[
          {
            label: `Maintenance (${openAlerts.length})`,
            id: "alerts",
            content: (
              <Table
                loading={loading}
                loadingText="Loading maintenance alerts..."
                header={
                  <Header variant="h2"
                    counter={`(${filteredAlerts.length === 0 ? 0 : (currentPage - 1) * pageSize + 1}-${Math.min(currentPage * pageSize, filteredAlerts.length)} of ${filteredAlerts.length})`}
                    actions={<Button iconName="refresh" onClick={fetchAlerts}>Refresh</Button>}>
                    {filterLabel}
                  </Header>
                }
                filter={
                  // Two filters: a Cloudscape Select dropdown for status
                  // (Active/All/Open/Scheduled/Completed) and the existing
                  // TextFilter for free-text search. Selecting a different
                  // status resets to page 1 so the operator doesn't end up
                  // looking at an empty page-N of a smaller filtered set.
                  <SpaceBetween direction="horizontal" size="s">
                    <Select
                      selectedOption={{
                        value: alertStatusFilter,
                        label:
                          alertStatusFilter === "ACTIVE" ? "Active (Open + Scheduled)" :
                          alertStatusFilter === "ALL" ? "All Statuses" :
                          alertStatusFilter === "OPEN" ? "Open Only" :
                          alertStatusFilter === "IN_PROGRESS" ? "Scheduled Only" :
                          "Completed",
                      }}
                      onChange={({ detail }) => {
                        setAlertStatusFilter(detail.selectedOption.value || "ACTIVE");
                        setCurrentPage(1);
                      }}
                      options={[
                        { value: "ACTIVE", label: "Active (Open + Scheduled)" },
                        { value: "ALL", label: "All Statuses" },
                        { value: "OPEN", label: "Open Only" },
                        { value: "IN_PROGRESS", label: "Scheduled Only" },
                        { value: "COMPLETED", label: "Completed" },
                      ]}
                    />
                    <TextFilter filteringText={filterText}
                      onChange={({ detail }) => { setFilterText(detail.filteringText); setCurrentPage(1); }}
                      filteringPlaceholder="Filter by vehicle, type, or message" />
                  </SpaceBetween>
                }
                pagination={
                  <Pagination
                    currentPageIndex={currentPage}
                    pagesCount={Math.ceil(filteredAlerts.length / pageSize)}
                    onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
                  />
                }
                preferences={
                  <CollectionPreferences
                    title="Preferences"
                    confirmLabel="Confirm"
                    cancelLabel="Cancel"
                    preferences={{ pageSize }}
                    pageSizePreference={{
                      title: 'Page size',
                      options: [
                        { value: 10, label: '10 alerts' },
                        { value: 20, label: '20 alerts' },
                        { value: 50, label: '50 alerts' },
                      ]
                    }}
                    onConfirm={({ detail }) => {
                      setPageSize(detail.pageSize || 20);
                      setCurrentPage(1);
                    }}
                  />
                }
                columnDefinitions={[
                  { id: "severity", header: "Severity", cell: (item) => {
                    // Service rows often have no severity context (the
                    // record represents work being done, not an alarm
                    // level). Show '—' instead of an empty 'stopped'
                    // StatusIndicator chip so the column reads cleanly.
                    if (!item.severity) return <Box color="text-body-secondary">—</Box>;
                    return (
                      <StatusIndicator type={
                        item.severity === "CRITICAL" ? "error" : item.severity === "HIGH" ? "warning" : item.severity === "MEDIUM" ? "info" : "stopped"
                      }>{item.severity}</StatusIndicator>
                    );
                  }, width: 95, sortingField: "priority" },
                  // VIN is the operator-facing identifier customers know
                  // their truck by. Three-step lookup: prefer the vin
                  // field on the alert (newer rows seed it), then resolve
                  // via vehicleVinMap (fetched once per page mount from
                  // /api/v1/vehicles for the Recalls + alert-schedule
                  // modals), and only fall back to the internal vehicleId
                  // if neither source has a VIN. The Link still navigates
                  // by vehicleId because that's the URL slug
                  // VehicleDetailView expects. Monospace renders the
                  // 17-char VIN compactly and makes 1/I and 0/O distinct.
                  { id: "vehicle", header: "VIN", cell: (item) => {
                    const display = item.vin || vehicleVinMap[item.vehicleId] || item.vehicleId;
                    return (
                      <Link href={`/vehicles/${item.vehicleId}`}>
                        <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace", fontSize: "12.5px" }}>
                          {display}
                        </span>
                      </Link>
                    );
                  }, width: 170 },
                  // Type: cleaned alertType label, rendered as a Link
                  // that opens the Alert Details modal on click. The
                  // operator gets the at-a-glance label inline and
                  // every other field on the alert (full message,
                  // trigger, repair guidance, predictive placeholder)
                  // is one click away. Replaces the dropped Message
                  // column — the full em-dash text + threshold tail
                  // now live inside the modal's Description section.
                  { id: "type", header: "Type", cell: (item) => (
                    <Link
                      onFollow={(e: any) => {
                        e.preventDefault();
                        openAlertDetailsModal(item);
                      }}
                      href="#"
                    >
                      {cleanAlertType(item.alertType)}
                    </Link>
                  ), width: 220 },
                  { id: "cost", header: "Est. Cost", cell: (item) => item.estimatedCost ? `$${item.estimatedCost}` : "—", width: 80 },
                  { id: "duration", header: "Hrs", cell: (item) => item.estimatedDuration || "—", width: 50 },
                  { id: "trend", header: "Trend", cell: (item) => {
                    // Trend isn't meaningful for service-history rows
                    // (they're not telemetry-derived). Render a muted
                    // em-dash for those instead of a misleading green
                    // 'success' StatusIndicator.
                    if (!item.trendDirection) return <Box color="text-body-secondary">—</Box>;
                    return (
                      <StatusIndicator type={item.trendDirection === "DEGRADING" ? "error" : item.trendDirection === "STABLE" ? "info" : "success"}>
                        {item.trendDirection}
                      </StatusIndicator>
                    );
                  }, width: 100 },
                  // Status column — added now that the table can mix
                  // open/scheduled/completed rows. Maps the underlying
                  // upper-cased status to a Cloudscape StatusIndicator
                  // type so the visual cue matches the colour the
                  // operator already associates with severity (red =
                  // attention, blue = work in progress, green = done).
                  { id: "status", header: "Status", cell: (item) => {
                    const s = item.status || "OPEN";
                    if (s === "COMPLETED" || s === "RESOLVED") return <StatusIndicator type="success">{s}</StatusIndicator>;
                    if (s === "IN_PROGRESS" || s === "SCHEDULED") return <StatusIndicator type="in-progress">{s}</StatusIndicator>;
                    if (s === "OPEN") return <StatusIndicator type="warning">OPEN</StatusIndicator>;
                    return <StatusIndicator type="pending">{s}</StatusIndicator>;
                  }, width: 110 },
                  { id: "actions", header: "Actions", cell: (item) => {
                    // Don't render a calendar icon on rows that no
                    // longer need scheduling. Mirrors the recalls
                    // Actions cell (em-dash for empty-vehicles case)
                    // and the vehicle detail Service tab pattern.
                    const s = item.status || "OPEN";
                    if (s === "COMPLETED" || s === "RESOLVED") {
                      return <Box color="text-body-secondary">—</Box>;
                    }
                    return (
                      <span title="Schedule service for this alert">
                        <Button
                          iconName="calendar"
                          variant="inline-icon"
                          ariaLabel="Schedule service for this alert"
                          onClick={() => openScheduleAlertModal(item)}
                        />
                      </span>
                    );
                  }, width: 80 },
                ]}
                items={filteredAlerts
                  .sort((a, b) => (a.priority || 99) - (b.priority || 99))
                  .slice((currentPage - 1) * pageSize, currentPage * pageSize)}
                variant="embedded"
                stickyHeader
              />
            ),
          },
          {
            label: `Recalls (${totalRecalls})`,
            id: "recalls",
            content: (
              <Container header={
                <Header variant="h2" counter={`(${totalRecalls})`}
                  description="Source: NHTSA Recalls API — matched against fleet VINs"
                  actions={<Button iconName="refresh">Check for New Recalls</Button>}>
                  Active Recalls
                </Header>
              }>
                <Table
                  columnDefinitions={[
                    { id: "id", header: "NHTSA #", cell: (item) => (
                      <Link href={`https://www.nhtsa.gov/recalls?nhtsaId=${item.id}`} external>{item.id}</Link>
                    ), width: 110 },
                    { id: "severity", header: "Severity", cell: (item) => (
                      <StatusIndicator type={item.severity === "Critical" ? "error" : item.severity === "High" ? "warning" : "info"}>{item.severity}</StatusIndicator>
                    ), width: 95 },
                    // NHTSA component strings are in 'CATEGORY:SUBSYSTEM:PART'
                    // form (e.g. 'STEERING:GEAR BOX (OTHER THAN RACK AND
                    // PINION)'). Surface the first two segments inline as
                    // the recognisable label and put the full string in a
                    // hover Popover so operators can read the original
                    // NHTSA wording without losing horizontal space.
                    { id: "component", header: "Component", cell: (item) => {
                      const full = String(item.component || "");
                      const parts = full.split(":").map((s) => s.trim()).filter(Boolean);
                      const head = parts.slice(0, 2).join(": ");
                      const truncated = parts.length > 2;
                      if (!truncated) return head;
                      return (
                        <Popover
                          dismissButton={false}
                          position="top"
                          size="large"
                          triggerType="text"
                          content={<Box variant="p">{full}</Box>}
                        >
                          <span style={{ cursor: "help" }}>{head}…</span>
                        </Popover>
                      );
                    }, width: 220 },
                    { id: "affected", header: "Affected", cell: (item) => (
                      <span>{item.confirmed} confirmed / {item.population} pop.</span>
                    ), width: 140 },
                    { id: "progress", header: "Progress", cell: (item) => {
                      const pct = item.affected > 0 ? Math.round(((item.completed + item.scheduled) / item.affected) * 100) : 0;
                      return <ProgressBar value={pct} additionalInfo={`${item.completed} done, ${item.scheduled} scheduled`} variant="key-value" />;
                    }, width: 180 },
                    // Compact summary of which fleet vehicles are affected.
                    // Inline cell shows just the count; full VIN list (with
                    // both VIN and internal vehicleId per row) is in a
                    // hover Popover so wide tables don't blow the layout.
                    { id: "vehicles", header: "Fleet Vehicles", cell: (item) => {
                      const vids: string[] = item.vehicles || [];
                      if (vids.length === 0) return <Box color="text-body-secondary">—</Box>;
                      return (
                        <Popover
                          dismissButton={false}
                          position="top"
                          size="large"
                          triggerType="text"
                          content={
                            <Box>
                              <Box variant="awsui-key-label" margin={{ bottom: "xs" }}>Affected vehicles</Box>
                              <SpaceBetween size="xxs">
                                {vids.map((vid) => {
                                  const vin = vehicleVinMap[vid];
                                  return (
                                    <Box key={vid} fontSize="body-s">
                                      <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace" }}>
                                        {vin || vid}
                                      </span>
                                      {vin && <Box color="text-body-secondary" display="inline" margin={{ left: "xs" }} fontSize="body-s">({vid})</Box>}
                                    </Box>
                                  );
                                })}
                              </SpaceBetween>
                            </Box>
                          }
                        >
                          <span style={{ cursor: "help" }}>{vids.length} {vids.length === 1 ? "vehicle" : "vehicles"}</span>
                        </Popover>
                      );
                    }, width: 130 },
                    { id: "actions", header: "Actions", cell: (item) => {
                      // Icon-only Schedule action across the app for visual
                      // consistency with the Maintenance Alerts tab. The
                      // calendar icon + native browser tooltip on the
                      // wrapping span ('Schedule recall service…') keeps
                      // the action discoverable on hover; the column
                      // header 'Actions' tells operators what the cell is
                      // for. Single-icon cell can't word-wrap.
                      const hasVehicles = (item.vehicles || []).length > 0;
                      if (!hasVehicles) {
                        return <Box color="text-body-secondary">—</Box>;
                      }
                      return (
                        <span title="Schedule recall service for affected VINs">
                          <Button
                            iconName="calendar"
                            variant="inline-icon"
                            ariaLabel="Schedule recall service for affected VINs"
                            onClick={() => openScheduleModal(item)}
                          />
                        </span>
                      );
                    }, width: 80 },
                  ]}
                  items={nhtsaRecalls}
                  variant="embedded"
                  stickyHeader
                />
              </Container>
            ),
          },
          {
            label: "Agent Activity",
            id: "agent",
            content: <RecallAgentFeed />,
          },
        ]}
      />
      </Container>

      {/*
       * Schedule Recall Service modal — opened from the Recalls tab's
       * Actions column. The form/submit logic lives in
       * <ScheduleRecallServiceModal /> so the same modal renders from
       * /recalls without copying the body. We only own visibility +
       * which recall is currently being scheduled. The vehicleVinMap
       * is passed in to avoid the modal re-fetching it (we already
       * fetched it on this page for the Recalls table's Fleet Vehicles
       * popover and the alert-schedule modal).
       */}
      <ScheduleRecallServiceModal
        visible={scheduleModalVisible}
        recall={scheduleRecall}
        onDismiss={() => setScheduleModalVisible(false)}
        vehicleVinMap={vehicleVinMap}
      />

      {/*
       * Alert Details modal — opened by clicking the Type cell of any
       * Maintenance Alerts row. Read-only consolidated view of every
       * operationally-useful field on the alert. Includes a Predictive
       * Insights placeholder section that future tire-wear / brake-
       * degradation models will fill with remaining-life estimates and
       * recommended service windows. Footer chains into the existing
       * single-alert Schedule Service modal so the operator can read
       * the diagnostic detail and dispatch service in one motion.
       */}
      <Modal
        visible={detailsModalVisible}
        onDismiss={() => setDetailsModalVisible(false)}
        size="large"
        header={
          detailsAlert ? (
            <SpaceBetween direction="horizontal" size="s">
              <span>{cleanAlertType(detailsAlert.alertType)}</span>
              <StatusIndicator
                type={
                  detailsAlert.severity === "CRITICAL" ? "error" :
                  detailsAlert.severity === "HIGH" ? "warning" :
                  detailsAlert.severity === "MEDIUM" ? "info" : "stopped"
                }
              >
                {detailsAlert.severity}
              </StatusIndicator>
              {detailsAlert.status && (
                <StatusIndicator
                  type={
                    detailsAlert.status === "COMPLETED" || detailsAlert.status === "RESOLVED" ? "success" :
                    detailsAlert.status === "IN_PROGRESS" || detailsAlert.status === "SCHEDULED" ? "in-progress" :
                    detailsAlert.status === "OPEN" ? "warning" : "pending"
                  }
                >
                  {detailsAlert.status}
                </StatusIndicator>
              )}
            </SpaceBetween>
          ) : (
            "Alert Details"
          )
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDetailsModalVisible(false)}>
                Close
              </Button>
              {detailsAlert && detailsAlert.status !== "COMPLETED" && detailsAlert.status !== "RESOLVED" && (
                <Button
                  variant="primary"
                  iconName="calendar"
                  onClick={() => {
                    // Chain into the existing single-alert schedule
                    // flow: close this modal first so we don't stack
                    // two modals, then open the schedule one with the
                    // same alert pre-loaded.
                    const a = detailsAlert;
                    setDetailsModalVisible(false);
                    openScheduleAlertModal(a);
                  }}
                >
                  Schedule service
                </Button>
              )}
            </SpaceBetween>
          </Box>
        }
      >
        {detailsAlert && (
          <SpaceBetween size="m">
            {/* Vehicle */}
            <Container header={<Header variant="h3">Vehicle</Header>}>
              <SpaceBetween size="xs">
                <Box>
                  <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace", fontSize: "13px" }}>
                    {detailsAlert.vin || vehicleVinMap[detailsAlert.vehicleId] || detailsAlert.vehicleId}
                  </span>
                  {(detailsAlert.vin || vehicleVinMap[detailsAlert.vehicleId]) && (
                    <Box display="inline" color="text-body-secondary" margin={{ left: "s" }}>
                      ({detailsAlert.vehicleId})
                    </Box>
                  )}
                </Box>
                <Link href={`/vehicles/${detailsAlert.vehicleId}`}>Open vehicle detail</Link>
              </SpaceBetween>
            </Container>

            {/* Description */}
            <Container header={<Header variant="h3">Description</Header>}>
              <Box variant="p">{detailsAlert.message || "No description provided."}</Box>
            </Container>

            {/* Trigger — only meaningful for telemetry-derived alerts.
                Service-history rows weren't fired by a telemetry
                threshold, so we skip the section entirely for those
                instead of rendering a wall of '—' fields. */}
            {detailsAlert.recordType !== 'SERVICE' && (
            <Container header={<Header variant="h3" description="Telemetry condition that fired this alert.">Trigger</Header>}>
              <ColumnLayout columns={3} variant="text-grid">
                <div>
                  <Box variant="awsui-key-label">Field</Box>
                  <Box>{detailsAlert.triggerField || "—"}</Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Condition</Box>
                  <Box>{detailsAlert.triggerCondition || "—"}</Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Trend</Box>
                  <Box>
                    <StatusIndicator type={detailsAlert.trendDirection === "DEGRADING" ? "error" : detailsAlert.trendDirection === "STABLE" ? "info" : "success"}>
                      {detailsAlert.trendDirection || "—"}
                    </StatusIndicator>
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Current value</Box>
                  <Box>{detailsAlert.currentValue || detailsAlert.currentValue === 0 ? String(detailsAlert.currentValue) : "—"}</Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Threshold</Box>
                  <Box>{detailsAlert.thresholdValue || detailsAlert.thresholdValue === 0 ? String(detailsAlert.thresholdValue) : "—"}</Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Days open</Box>
                  <Box>{detailsAlert.daysOpen || 0}</Box>
                </div>
              </ColumnLayout>
            </Container>
            )}

            {/* Service info — only for service-history rows. Surfaces
                the booked dealer/provider, scheduled or completed
                date, and any back-link to the original alert/recall
                that this work was scheduled to resolve. */}
            {detailsAlert.recordType === 'SERVICE' && (
            <Container header={<Header variant="h3" description="Service appointment context.">Service</Header>}>
              <ColumnLayout columns={2} variant="text-grid">
                <div>
                  <Box variant="awsui-key-label">Provider</Box>
                  <Box>{detailsAlert.provider || "—"}</Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Service date</Box>
                  <Box>{detailsAlert.createdDate ? new Date(detailsAlert.createdDate).toLocaleDateString() : "—"}</Box>
                </div>
                {detailsAlert.manualReference && (
                  <div>
                    <Box variant="awsui-key-label">Source</Box>
                    <Box>{detailsAlert.manualReference}</Box>
                  </div>
                )}
              </ColumnLayout>
            </Container>
            )}

            {/* Service recommendation — repair guidance is alert-only
                (the maintenance processor populates these fields).
                Service rows already surfaced cost + provider + source
                in the Service section above; no need to repeat. */}
            {detailsAlert.recordType !== 'SERVICE' && (
            <Container header={<Header variant="h3" description="Generated by the maintenance processor for technician hand-off.">Service recommendation</Header>}>
              <SpaceBetween size="s">
                <ColumnLayout columns={2} variant="text-grid">
                  <div>
                    <Box variant="awsui-key-label">Estimated cost</Box>
                    <Box>{detailsAlert.estimatedCost ? `$${detailsAlert.estimatedCost.toLocaleString()}` : "—"}</Box>
                  </div>
                  <div>
                    <Box variant="awsui-key-label">Estimated duration</Box>
                    <Box>{detailsAlert.estimatedDuration ? `${detailsAlert.estimatedDuration} hr${detailsAlert.estimatedDuration === 1 ? "" : "s"}` : "—"}</Box>
                  </div>
                </ColumnLayout>
                {detailsAlert.repairInstructions && (
                  <div>
                    <Box variant="awsui-key-label">Repair instructions</Box>
                    <Box variant="p">{detailsAlert.repairInstructions}</Box>
                  </div>
                )}
                {detailsAlert.requiredTools && (
                  <div>
                    <Box variant="awsui-key-label">Required tools</Box>
                    <Box variant="p">{detailsAlert.requiredTools}</Box>
                  </div>
                )}
                {detailsAlert.manualReference && (
                  <div>
                    <Box variant="awsui-key-label">Manual reference</Box>
                    <Box variant="p">{detailsAlert.manualReference}</Box>
                  </div>
                )}
                {detailsAlert.safetyWarnings && (
                  <Alert type="warning" header="Safety">
                    {detailsAlert.safetyWarnings}
                  </Alert>
                )}
              </SpaceBetween>
            </Container>
            )}

            {/*
             * Predictive Insights — placeholder for the upcoming
             * tire-wear and brake-degradation models. Today this
             * section renders an empty state explaining what's
             * coming. When those models ship, the contents become a
             * <PredictiveInsightsPanel alert={detailsAlert} /> that
             * conditionally renders remaining-life estimates,
             * confidence intervals, and recommended service windows
             * based on the alert.alertType + the vehicle's recent
             * telemetry trend. The empty state stays as the fallback
             * for alert types the model doesn't cover.
             */}
            <Container header={
              <Header
                variant="h3"
                description="Forecasts based on telemetry trends + historical wear patterns."
              >
                Predictive Insights
              </Header>
            }>
              <Box textAlign="center" padding="l" color="text-body-secondary">
                <SpaceBetween size="xs">
                  <Box variant="strong" color="text-body-secondary">Coming soon</Box>
                  <Box variant="p" color="text-body-secondary">
                    Predictive remaining-life estimates aren't available for this alert type yet.
                    Tire-wear and brake-degradation models are next on the roadmap — once
                    deployed they'll surface here as expected-replacement-mileage,
                    confidence intervals, and recommended service windows.
                  </Box>
                </SpaceBetween>
              </Box>
            </Container>
          </SpaceBetween>
        )}
      </Modal>

      {/*
       * Schedule Service modal for a single maintenance alert. Opened
       * from the calendar icon on each Open Maintenance Alerts row. Same
       * backend endpoint as the recalls flow but single-VIN, so no
       * Multiselect — the alert's vehicle is captured directly from the
       * row context. Description carries the cleaned alertType + the
       * head segment of the message so the dispatcher knows what work
       * is being requested without round-tripping back to the alerts
       * table.
       */}
      <Modal
        visible={alertScheduleVisible}
        onDismiss={() => !alertScheduleSubmitting && setAlertScheduleVisible(false)}
        size="medium"
        header="Schedule Service"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setAlertScheduleVisible(false)}
                disabled={alertScheduleSubmitting}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={submitAlertSchedule}
                loading={alertScheduleSubmitting}
                disabled={!alertScheduleDealer || !alertScheduleDate || alertScheduleSubmitting}
              >
                {alertScheduleSubmitting ? "Scheduling…" : "Schedule service"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {alertScheduleAlert && (
          <Form>
            <SpaceBetween size="m">
              <Container>
                <SpaceBetween size="xs">
                  <Box variant="awsui-key-label">Vehicle</Box>
                  <Box>
                    <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace" }}>
                      {alertScheduleAlert.vin || vehicleVinMap[alertScheduleAlert.vehicleId] || alertScheduleAlert.vehicleId}
                    </span>
                    {(alertScheduleAlert.vin || vehicleVinMap[alertScheduleAlert.vehicleId]) && (
                      <Box display="inline" color="text-body-secondary" margin={{ left: "s" }}>
                        ({alertScheduleAlert.vehicleId})
                      </Box>
                    )}
                  </Box>
                  <Box variant="awsui-key-label" margin={{ top: "s" }}>Alert</Box>
                  <Box>
                    <StatusIndicator
                      type={
                        alertScheduleAlert.severity === "CRITICAL" ? "error" :
                        alertScheduleAlert.severity === "HIGH" ? "warning" :
                        alertScheduleAlert.severity === "MEDIUM" ? "info" : "stopped"
                      }
                    >
                      {alertScheduleAlert.severity}
                    </StatusIndicator>
                    <Box display="inline" margin={{ left: "s" }}>
                      {String(alertScheduleAlert.alertType || "")
                        .replace(/^[^.]+\./, "")
                        .replace(/_/g, " ")
                        .replace(/\b\w/g, (c) => c.toUpperCase())}
                    </Box>
                  </Box>
                  <Box color="text-body-secondary" fontSize="body-s">
                    {String(alertScheduleAlert.message || "").split(/\s+—\s+|\s+--\s+/)[0]}
                  </Box>
                </SpaceBetween>
              </Container>

              <FormField
                label="Service center"
                description="Where the work will be performed. The booked appointment shows up under this provider name on the vehicle's Service tab."
              >
                <Select
                  selectedOption={alertScheduleDealer as any}
                  onChange={({ detail }) => setAlertScheduleDealer(detail.selectedOption as any)}
                  options={SERVICE_CENTER_OPTIONS}
                  placeholder="Choose a service center…"
                  triggerVariant="option"
                />
              </FormField>

              <FormField label="Service date" description="Operator-confirmed appointment date.">
                <DatePicker
                  value={alertScheduleDate}
                  onChange={({ detail }) => setAlertScheduleDate(detail.value)}
                  placeholder="YYYY-MM-DD"
                />
              </FormField>

              <FormField
                label="Notes"
                description="Optional. Anything the dispatcher or service center should know."
              >
                <Textarea
                  value={alertScheduleNotes}
                  onChange={({ detail }) => setAlertScheduleNotes(detail.value)}
                  placeholder="e.g. Drop truck Monday 7am, contact dispatcher on arrival…"
                  rows={3}
                />
              </FormField>

              {alertScheduleError && (
                <Alert type="error" header="Could not schedule service">
                  {alertScheduleError}
                </Alert>
              )}
            </SpaceBetween>
          </Form>
        )}
      </Modal>
    </SpaceBetween>
  );
};

export default ServiceDashboard;
