// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Connected Services card (spec `2026-09-10-cms-connected-services-consumer`,
 * D5 / T2.4).
 *
 * One card on Vehicle Detail, not a new screen. Two primary states:
 *
 *   enrolled     → last record timestamp, expandable raw-JSON payload, unenroll
 *   not enrolled → "Not enrolled in Connected Services feed", enroll
 *
 * Plus four states that are NOT either of those and must not be rendered as
 * "no telemetry", because each is a different fact about the world:
 *
 *   notConfigured        this stage has no producer wired (a supported state)
 *   producerUnavailable  the producer did not answer
 *   shapeDrift           CMS could not parse the feed
 *   unresolved           the VIN is in scope but resolves to no CMS vehicle
 *
 * That distinction is the whole reason this card does not have a single error
 * branch. `_cs_filter_feed`'s docstring in index.py makes the same argument on
 * the backend: a missing `records` key read as an empty list turns "we could not
 * read the feed" into "this vehicle has no telemetry", and the second is a
 * statement about the vehicle that CMS has no basis for.
 *
 * Calls CMS's three proxy routes ONLY, via `@/api/connectedServices`. The
 * producer's API is never addressed from the browser — see that module's header
 * and `src/__tests__/connectedServicesBundleHygiene.test.ts`.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Container,
  ExpandableSection,
  Header,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from '@cloudscape-design/components';

import {
  ConnectedServicesError,
  deriveEnrollment,
  enrollVin,
  getSubscriptionFeed,
  unenrollVin,
  type ConnectedServicesFailureKind,
  type ConnectedServicesRecord,
  type EnrollmentView,
} from '@/api/connectedServices';

export interface ConnectedServicesCardProps {
  /** The vehicle's VIN. The three routes are VIN-addressed, not vehicleId-addressed. */
  vin?: string;
}

/**
 * Epoch **milliseconds** → locale string.
 *
 * The producer's `timestamp` is ms (verified against the live capture in
 * `producer-live-shapes.json`, e.g. 1789245554121). Elsewhere in
 * `VehicleDetailView.tsx` a `> 9999999999` heuristic distinguishes seconds from
 * ms because those sources genuinely carry both; this one does not, so guessing
 * here would only create a way to be wrong about a value we know.
 */
export function formatRecordTimestamp(epochMs: number): string {
  if (!Number.isFinite(epochMs)) return 'Unknown';
  return new Date(epochMs).toLocaleString();
}

/**
 * Fixed copy per failure kind. **These are the only strings the failure branch
 * renders**, and that is a security property, not a style choice.
 *
 * `ConnectedServicesError.message` carries `body.detail || body.message ||
 * body.error` from the proxy response — producer-influenced text. React escapes
 * it, so there is no XSS vector, but it can carry internal error text, upstream
 * hostnames, or a stack fragment, and any authenticated CMS user would see it.
 * Adding a "technical details" line here reads like a UX improvement and is a
 * disclosure change; `test_failure_copy_never_renders_the_error_message` fails
 * if one is added, so the decision cannot be made silently.
 */
const FAILURE_COPY: Record<
  ConnectedServicesFailureKind,
  { type: 'error' | 'warning' | 'info'; header: string; body: string }
> = {
  notConfigured: {
    type: 'info',
    header: 'Connected Services is not configured on this environment',
    body:
      'No subscription producer is wired to this stage, so there is nothing to '
      + 'enroll into. This is a deployment state, not a problem with this vehicle.',
  },
  producerUnavailable: {
    type: 'error',
    header: 'Connected Services feed is unavailable',
    body:
      'The subscription producer did not answer. This vehicle\u2019s telemetry '
      + 'status is unknown \u2014 not absent.',
  },
  producerUnauthorized: {
    type: 'error',
    header: 'Connected Services feed rejected CMS\u2019s credential',
    body:
      'The producer refused CMS\u2019s subscriber credential. An operator needs '
      + 'to check the credential, not this vehicle.',
  },
  shapeDrift: {
    type: 'error',
    header: 'Connected Services feed could not be read',
    body:
      'The producer answered in a shape CMS does not recognise, so the feed was '
      + 'not parsed. This vehicle\u2019s telemetry status is unknown \u2014 not absent.',
  },
  forbidden: {
    type: 'warning',
    header: 'This vehicle is outside your fleet scope',
    body:
      'Enrolling or unenrolling this VIN is not permitted for your fleet '
      + 'assignment, or the VIN does not resolve to a vehicle in CMS.',
  },
  invalidVin: {
    type: 'warning',
    header: 'VIN is not in a valid format',
    body: 'CMS could not accept this VIN as a 17-character ISO 3779 value.',
  },
  network: {
    type: 'error',
    header: 'Could not reach CMS',
    body: 'The request did not complete. Retry, then check your connection.',
  },
  unknown: {
    type: 'error',
    header: 'Connected Services request failed',
    body: 'CMS returned an unexpected response.',
  },
};

const HEADER = (
  <Header
    variant="h3"
    description="Telemetry delivered to CMS through its own Connected Services subscription"
  >
    Connected Services
  </Header>
);

export const ConnectedServicesCard: React.FC<ConnectedServicesCardProps> = ({ vin }) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [mutating, setMutating] = useState<boolean>(false);
  const [view, setView] = useState<EnrollmentView | null>(null);
  const [failure, setFailure] = useState<ConnectedServicesError | null>(null);
  // Set only by a SUCCESSFUL enroll, and only until the next refresh resolves.
  // A scoped operator's filtered `vins_in_scope` cannot show a record-less
  // enrollment (see `deriveEnrollment`), so without this the card would answer
  // "not enrolled" immediately after the enroll it just confirmed.
  const [justEnrolled, setJustEnrolled] = useState<boolean>(false);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!vin) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const feed = await getSubscriptionFeed();
      setView(deriveEnrollment(feed, vin));
      setFailure(null);
    } catch (err) {
      setFailure(
        err instanceof ConnectedServicesError
          ? err
          : new ConnectedServicesError('Connected Services request failed', 'unknown', 0),
      );
      setView(null);
    } finally {
      setLoading(false);
    }
  }, [vin]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onEnroll = useCallback(async () => {
    if (!vin) return;
    setMutating(true);
    setActionNote(null);
    try {
      const result = await enrollVin(vin);
      // `already_present` is a normal no-op, not a failure — a re-enroll of a
      // VIN someone else already added is the expected shape, not an error.
      setActionNote(
        result.added?.includes(vin)
          ? `Enrolled. Subscription scope is now ${result.scope_size} VIN(s).`
          : `Already enrolled. Subscription scope is ${result.scope_size} VIN(s).`,
      );
      setJustEnrolled(true);
      setFailure(null);
      await refresh();
    } catch (err) {
      setFailure(
        err instanceof ConnectedServicesError
          ? err
          : new ConnectedServicesError('Enroll failed', 'unknown', 0),
      );
    } finally {
      setMutating(false);
    }
  }, [vin, refresh]);

  const onUnenroll = useCallback(async () => {
    if (!vin) return;
    setMutating(true);
    setActionNote(null);
    try {
      const result = await unenrollVin(vin);
      // `removed: false` means the VIN was already absent. Saying "unenrolled"
      // would claim CMS changed something it did not.
      setActionNote(
        result.removed
          ? `Unenrolled. Subscription scope is now ${result.scope_size} VIN(s).`
          : `Already not enrolled. Subscription scope is ${result.scope_size} VIN(s).`,
      );
      setJustEnrolled(false);
      setFailure(null);
      await refresh();
    } catch (err) {
      setFailure(
        err instanceof ConnectedServicesError
          ? err
          : new ConnectedServicesError('Unenroll failed', 'unknown', 0),
      );
    } finally {
      setMutating(false);
    }
  }, [vin, refresh]);

  if (!vin) {
    return (
      <Container header={HEADER}>
        <Box color="text-status-inactive" padding="s" data-testid="cs-no-vin">
          This vehicle has no VIN recorded, so it cannot be addressed in a
          Connected Services subscription.
        </Box>
      </Container>
    );
  }

  if (loading && view === null && failure === null) {
    return (
      <Container header={HEADER}>
        <Box textAlign="center" padding="l" data-testid="cs-loading">
          <Spinner size="normal" />
          <Box variant="p" color="text-body-secondary">
            Loading Connected Services status&hellip;
          </Box>
        </Box>
      </Container>
    );
  }

  if (failure !== null) {
    const copy = FAILURE_COPY[failure.kind] ?? FAILURE_COPY.unknown;
    return (
      <Container header={HEADER}>
        <SpaceBetween size="s">
          <Alert type={copy.type} header={copy.header} data-testid={`cs-failure-${failure.kind}`}>
            {copy.body}
          </Alert>
          <Button onClick={() => void refresh()} loading={loading} data-testid="cs-retry">
            Retry
          </Button>
        </SpaceBetween>
      </Container>
    );
  }

  const enrolled = (view?.enrolled ?? false) || justEnrolled;
  const latest: ConnectedServicesRecord | null = view?.latestRecord ?? null;

  return (
    <Container header={HEADER}>
      <SpaceBetween size="s">
        {actionNote !== null && (
          <Box variant="small" color="text-body-secondary" data-testid="cs-action-note">
            {actionNote}
          </Box>
        )}

        {view?.cachedAt !== undefined && view.cachedAt !== '' && (
          // A cached feed says so. Presenting a cached answer as live is a
          // correctness bug, not a cosmetic one — the operator's next decision
          // depends on how old this is. Same reason a Tier 2 artifact carries
          // `computed_at` and surfaces it.
          <Box variant="small" color="text-status-inactive" data-testid="cs-cached-at">
            Served from cache, as of {view.cachedAt}
          </Box>
        )}

        {view?.unresolved === true && (
          <Alert type="info" header="In scope, not resolvable" data-testid="cs-unresolved">
            This VIN is in CMS&rsquo;s subscription scope but does not resolve to a
            vehicle record in CMS. Telemetry is being delivered and cannot be
            attributed.
          </Alert>
        )}

        {!enrolled && (
          <>
            <StatusIndicator type="stopped" data-testid="cs-not-enrolled">
              Not enrolled in Connected Services feed
            </StatusIndicator>
            <Button
              variant="primary"
              onClick={() => void onEnroll()}
              loading={mutating}
              data-testid="cs-enroll"
            >
              Enroll
            </Button>
          </>
        )}

        {enrolled && (
          <>
            <StatusIndicator type="success" data-testid="cs-enrolled">
              Enrolled in Connected Services feed
            </StatusIndicator>

            {latest !== null ? (
              <>
                <Box variant="p" data-testid="cs-last-record">
                  Last record: {formatRecordTimestamp(latest.timestamp)}
                  {latest.dataSourceRoute ? ` \u00b7 route ${latest.dataSourceRoute}` : ''}
                  {` \u00b7 ${view?.recordCount ?? 0} record(s) in the current feed`}
                </Box>
                <ExpandableSection
                  headerText="View latest record"
                  data-testid="cs-payload-section"
                >
                  <Box variant="code" data-testid="cs-payload">
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify(latest, null, 2)}
                    </pre>
                  </Box>
                </ExpandableSection>
              </>
            ) : (
              // Enrolled, zero records. NOT an error, and NOT "no telemetry
              // exists" — the feed is a rolling window and an enrollment made
              // seconds ago has produced nothing yet.
              <Box color="text-status-inactive" data-testid="cs-enrolled-no-records">
                No telemetry records for this VIN in the current feed window yet.
              </Box>
            )}

            <Button onClick={() => void onUnenroll()} loading={mutating} data-testid="cs-unenroll">
              Unenroll
            </Button>
          </>
        )}
      </SpaceBetween>
    </Container>
  );
};

export default ConnectedServicesCard;
