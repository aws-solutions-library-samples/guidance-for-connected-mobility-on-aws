// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Dealer options for the service-scheduling pickers.
//
// Spec `2026-09-02-cms-dms-service-convergence` T4.2. Replaces
// `SERVICE_CENTER_OPTIONS`, an exported array of six invented service centres
// that had already been emptied to `[]` — leaving both scheduling pickers
// offering nothing while still looking like working forms.
//
// ## Why a fetch and not a constant
//
// D6: DMS's twelve rooftops are canonical for dealer identity, and only an
// authorised rooftop performs warranty or recall work. The hardcoded six
// encoded the right rule with invented data. Hardcoding the twelve here instead
// would, in T4.2's own words, "replace one fake registry with another" — and it
// would silently rot the day DMS's roster changes.
//
// ## Why `/api/v1/dealers` and not the DMS API directly
//
// D3: the browser holds a token DMS would accept, so browser-direct would work.
// It is rejected anyway — it needs CORS on the DMS API, and it re-couples the
// CMS bundle to a second API base URL. `config/api.ts` carries a standing
// warning against using `dmsUiUrl` to reach the DMS API for the same reason.
// CMS's own backend reads DMS server-side, forwarding the caller's token so DMS
// authorizes the end user.
//
// ## The state model is the point (D5)
//
// Four states, and they are deliberately NOT collapsible into "options plus a
// boolean". `error` and `empty` must be distinguishable by the caller, because
// "we could not reach the dealer directory" and "the directory has no rooftops"
// warrant different words in front of a user, and rendering the first as the
// second is the fabricated-fact defect this spec exists to remove. There is no
// fallback list on any path.

import { useCallback, useEffect, useState } from "react";
import { getApiEndpoint } from "../config/api";
import { authFetch } from "../utils/authFetch";

/** A rooftop, in the shape the Cloudscape `Select` consumes. */
export interface DealerOption {
  label: string;
  value: string;
  description: string;
}

/** Raw item shape returned by `GET /api/v1/dealers`.
 *
 * snake_case because it is DMS's projection passed through unchanged by the CMS
 * proxy. Renaming it at the boundary would be a second mapping to keep in sync;
 * the mapping to `DealerOption` below is the one place the shape is adapted.
 */
interface DealerApiItem {
  dealer_id?: string;
  dealer_name?: string;
  district_id?: string;
}

export type DealerOptionsStatus = "loading" | "ready" | "empty" | "error";

export interface UseDealerOptionsResult {
  options: DealerOption[];
  status: DealerOptionsStatus;
  /** Human-readable failure reason. Non-empty only when status === "error". */
  errorMessage: string;
  /** Re-run the fetch. Lets a caller offer a retry instead of a dead form. */
  reload: () => void;
}

/** Placeholder text for a Cloudscape `Select`, derived from the state.
 *
 * Exported so both consumers word the four states identically — two pickers
 * drifting apart on how they describe a failure is how one of them ends up
 * saying "no service centres" when the truth is "we could not ask".
 */
export function dealerPlaceholder(status: DealerOptionsStatus): string {
  switch (status) {
    case "loading":
      return "Loading service centres…";
    case "error":
      return "Service centres unavailable";
    case "empty":
      return "No authorised service centres available";
    default:
      return "Choose a service centre…";
  }
}

/**
 * Fetch the authorised service centres a fleet manager may book at.
 *
 * @param enabled - When false the hook stays in `loading` and issues no
 *   request. Both consumers are modals, and firing the request on mount for a
 *   modal the operator may never open is a needless call on every page load.
 */
export function useDealerOptions(enabled: boolean = true): UseDealerOptionsResult {
  const [options, setOptions] = useState<DealerOption[]>([]);
  const [status, setStatus] = useState<DealerOptionsStatus>("loading");
  const [errorMessage, setErrorMessage] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const run = async () => {
      setStatus("loading");
      setErrorMessage("");
      try {
        const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
        const resp = await authFetch(`${apiEndpoint}/api/v1/dealers`);
        if (!resp.ok) {
          // Distinguish "not permitted" from "broken". The backend passes DMS's
          // 401/403 through precisely so this branch can exist: a fleet manager
          // whose grants are not yet provisioned is not an outage, and telling
          // them it is sends them to the wrong place for help.
          const reason =
            resp.status === 403 || resp.status === 401
              ? "You do not have access to the dealer directory."
              : `Dealer directory returned ${resp.status}.`;
          if (!cancelled) {
            setOptions([]);
            setErrorMessage(reason);
            setStatus("error");
          }
          return;
        }
        const data = await resp.json();
        const items: DealerApiItem[] = Array.isArray(data?.dealers) ? data.dealers : [];
        const mapped: DealerOption[] = items
          // A row with no id cannot be booked against — `create_fleet_repair_order`
          // validates `dealer_id` against DMS's roster and would reject it. Drop
          // it rather than offering an option whose selection fails.
          .filter((d) => Boolean(d.dealer_id))
          .map((d) => ({
            value: String(d.dealer_id),
            // Fall back to the id ONLY for the display label, and only when the
            // name is genuinely absent. Showing the raw id is honest; inventing
            // a friendly name from it would not be.
            label: d.dealer_name ? String(d.dealer_name) : String(d.dealer_id),
            description: d.district_id ? String(d.district_id) : "",
          }));
        if (!cancelled) {
          setOptions(mapped);
          // An authoritative empty answer is its own state, not an error and not
          // a silent blank picker.
          setStatus(mapped.length > 0 ? "ready" : "empty");
        }
      } catch (e) {
        if (!cancelled) {
          setOptions([]);
          setErrorMessage(
            e instanceof Error && e.message
              ? `Could not load service centres: ${e.message}`
              : "Could not load service centres.",
          );
          setStatus("error");
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [enabled, reloadToken]);

  return { options, status, errorMessage, reload };
}

export default useDealerOptions;
