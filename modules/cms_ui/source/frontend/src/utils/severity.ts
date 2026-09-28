/**
 * severity.ts — canonical severity display helpers.
 *
 * Background (2026-05-04):
 *   The CMS has two severity conventions that used to be rendered
 *   interchangeably in the UI, which confused operators:
 *
 *   1. Event catalog (cms-<stage>-event-catalog.severity)
 *      Numeric 1-4, REVERSE-RANKED where higher = worse:
 *         4 = CRITICAL   (paired with severity_hint=P0)
 *         3 = HIGH       (paired with severity_hint=P1)
 *         2 = MEDIUM     (paired with severity_hint=P2)
 *         1 = LOW        (paired with severity_hint=P3)
 *
 *   2. DTC history + safety events + maintenance alerts
 *      String labels: "CRITICAL" / "HIGH" / "MEDIUM" / "LOW".
 *
 *   When the UI renders "Severity: 2" in a simulator dropdown or a
 *   campaign wizard, operators reasonably assume "2 of 4 = medium-high"
 *   when it actually means the opposite of a pain scale (1 is least
 *   urgent). We collapse both conventions to the human-readable string
 *   at render time to eliminate the ambiguity.
 *
 * Keep this helper display-only. Do not use it to transform values
 * that get persisted back to DDB — downstream consumers (Flink jobs,
 * classifiers) still expect the raw numeric or raw string forms.
 */

export type SeverityLabel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';

/**
 * Normalize any of the severity representations the backend may emit
 * into a canonical human-readable string.
 *
 * Accepts:
 *  - numeric  4 | 3 | 2 | 1           (event catalog's reverse-ranked field)
 *  - string   "4" | "3" | "2" | "1"   (stringified numeric)
 *  - string   "P0" | "P1" | "P2" | "P3" (VSA severity_hint)
 *  - string   "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" (already canonical, any case)
 *  - null/undefined/anything-else     → "UNKNOWN"
 */
export function severityLabel(raw: unknown): SeverityLabel {
  if (raw === null || raw === undefined || raw === '') return 'UNKNOWN';

  // Numeric (or numeric-as-string) path. In the event catalog the
  // numeric scale is reverse-ranked (higher = worse), so flip.
  const asNum = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isNaN(asNum)) {
    if (asNum >= 4) return 'CRITICAL';
    if (asNum === 3) return 'HIGH';
    if (asNum === 2) return 'MEDIUM';
    if (asNum <= 1) return 'LOW';
  }

  // String-based paths.
  const s = String(raw).trim().toUpperCase();
  switch (s) {
    case 'P0':
    case 'CRITICAL':
      return 'CRITICAL';
    case 'P1':
    case 'HIGH':
      return 'HIGH';
    case 'P2':
    case 'MEDIUM':
    case 'MED':
      return 'MEDIUM';
    case 'P3':
    case 'LOW':
      return 'LOW';
    default:
      return 'UNKNOWN';
  }
}

/** Convenience: returns true when the severity resolves to CRITICAL or HIGH. */
export function isHighOrCritical(raw: unknown): boolean {
  const s = severityLabel(raw);
  return s === 'CRITICAL' || s === 'HIGH';
}
