# Technology Reference: Connected Services UI

Verified against installed packages — do not substitute DMS's `docs/tech.md`, which is pinned
to `@cloudscape-design/components@3.0.839` and differs from this module's `3.0.1354`.

**Verified against:**
- `@cloudscape-design/components@3.0.1354`
  (`node_modules/@cloudscape-design/components/package.json`)
- `@cloudscape-design/collection-hooks@1.0.107`
  (`node_modules/@cloudscape-design/collection-hooks/package.json`)
- `react-router-dom@6.30.6`
- `react@18.3.1`

Verification date: 2026-09-04

---

## (a) `useCollection` — Signature and `UseCollectionResult<T>` Shape

**Sources:**
- `node_modules/@cloudscape-design/collection-hooks/mjs/use-collection.d.ts` (line 2)
- `node_modules/@cloudscape-design/collection-hooks/mjs/interfaces.d.ts` (full file)

### Signature

```typescript
// node_modules/@cloudscape-design/collection-hooks/mjs/use-collection.d.ts:2
function useCollection<T>(
  allItems: ReadonlyArray<T>,
  options: UseCollectionOptions<T>
): UseCollectionResult<T>
```

### `UseCollectionOptions<T>`

```typescript
// node_modules/@cloudscape-design/collection-hooks/mjs/interfaces.d.ts lines ~31-73
interface UseCollectionOptions<T> {
  filtering?: {
    filteringFunction?: (item: T, filteringText: string, filteringFields?: string[]) => boolean;
    fields?: string[];
    empty?: React.ReactNode;       // shown when allItems.length === 0
    noMatch?: React.ReactNode;     // shown when allItems.length > 0 but filter matches nothing
    defaultFilteringText?: string;
  };
  propertyFiltering?: { ... };
  sorting?: {
    defaultState?: SortingState<T>;
    multiColumn?: boolean;                      // @defaultValue false
    defaultSortingColumns?: ReadonlyArray<SortingState<T>>;
  };
  pagination?: {
    defaultPage?: number;
    pageSize?: number;
    allowPageOutOfRange?: boolean;
  };
  selection?: {
    defaultSelectedItems?: ReadonlyArray<T>;
    keepSelection?: boolean;
    trackBy?: TrackBy<T>;
  };
  expandableRows?: ExpandableRowsProps<T>;
}
```

### `UseCollectionResult<T>` Shape

```typescript
// node_modules/@cloudscape-design/collection-hooks/mjs/interfaces.d.ts lines ~104-140
interface UseCollectionResult<T> {
  // Current page items (filtered + sorted + paginated)
  items: ReadonlyArray<T>;
  // All items after filter+sort, before pagination
  allPageItems: ReadonlyArray<T>;
  // Count after filtering; undefined when no filter is active
  filteredItemsCount: number | undefined;
  actions: CollectionActions<T>;   // setFiltering, setCurrentPage, setSorting, ...
  collectionProps: {
    empty?: React.ReactNode;       // pre-resolved from filtering.empty / noMatch
    onSortingChange?: ...;
    sortingColumn?: SortingColumn<T>;
    sortingDescending?: boolean;
    selectedItems?: ReadonlyArray<T>;
    onSelectionChange?: ...;
    ref: React.RefObject<CollectionRef>;
    totalItemsCount: number;
    firstIndex: number;
  };
  filterProps: {
    filteringText: string;
    onChange(event: { detail: { filteringText: string } }): void;
    disabled?: boolean;
  };
  paginationProps: {
    currentPageIndex: number;
    pagesCount: number;            // added by UseCollectionResult (extends base)
    onChange(event: { detail: { currentPageIndex: number } }): void;
    disabled?: boolean;
  };
  propertyFilterProps: { ... };
}
```

**`collectionProps.empty` resolution (from `node_modules/@cloudscape-design/collection-hooks/mjs/utils.js` lines 92-101):**
```javascript
// allItems here is the ORIGINAL input passed to useCollection(), not the filtered result
let empty = options.filtering
  ? allItems.length
    ? options.filtering.noMatch   // items exist but filter produced 0 results
    : options.filtering.empty     // no items exist at all
  : null;
```

---

## (b) `AppLayout` — `headerSelector`, `contentType`, `navigationWidth`, `navigationOpen` / `onNavigationChange`

**Source:** `node_modules/@cloudscape-design/components/app-layout/interfaces.d.ts`

```typescript
// app-layout/interfaces.d.ts — BaseLayoutProps
interface BaseLayoutProps {
  /**
   * CSS selector for the application header.
   * AppLayout reads this selector to learn the header's height for sticky offset
   * calculations. It does NOT create a spacer and does NOT move AppLayout's top edge.
   */
  headerSelector?: string;

  /**
   * Adjusts content margins and spacing based on page layout.
   * @type 'default' | 'form' | 'table' | 'cards' | 'wizard' | 'dashboard'
   */
  contentType?: AppLayoutProps.ContentType;

  /** Side navigation width in pixels. */
  navigationWidth?: number;

  /** Whether the navigation drawer is open (controlled). */
  navigationOpen?: boolean;

  /** Fired when the navigation drawer is toggled. detail: { open: boolean } */
  onNavigationChange?: NonCancelableEventHandler<AppLayoutProps.ChangeDetail>;

  /** If true, the navigation drawer is not displayed at all. */
  navigationHide?: boolean;

  /** If true, the tools drawer is not displayed at all. */
  toolsHide?: boolean;
}

// AppLayoutPropsWithDefaults (app-layout/interfaces.d.ts, last type):
// navigationOpen is listed as SomeRequired, confirming it has a default.
// Default values from app-layout/defaults.d.ts applyDefaults():
//   navigationOpen per contentType — 'table'/'cards' default open=false on first render
```

**Key note on `headerSelector`:** It only reports header height; it does not substitute a spacer div. Use `position: sticky` (not `fixed`) for the header element to avoid requiring a spacer.

---

## (c) `SideNavigation` — `items` shape for `type: 'section'`

**Source:** `node_modules/@cloudscape-design/components/side-navigation/interfaces.d.ts`

```typescript
// SideNavigationProps.Section
interface Section {
  type: 'section';
  text: string;                       // section heading
  items: ReadonlyArray<Item>;         // any valid SideNavigation item
  defaultExpanded?: boolean;          // default: true (expands on mount)
}

// SideNavigationProps.Item union:
type Item = Divider | Link | Section | LinkGroup | ExpandableLinkGroup | SectionGroup;

// The link type used inside a section:
interface Link {
  type: 'link';
  text: string;
  href: string;
  external?: boolean;
  externalIconAriaLabel?: string;
  info?: React.ReactNode;
  icon?: React.ReactNode;
}
```

When `collapsed={true}` on `SideNavigation`, section children are NOT rendered (docstring on line ~116: "Section, `Section group`, `Link group`, and `Expandable link group` children are not rendered").

---

## (d) `Alert` — `dismissible` default: confirm omitting yields non-dismissible

**Source:** `node_modules/@cloudscape-design/components/alert/interfaces.d.ts` and
`node_modules/@cloudscape-design/components/alert/internal.js` (line 32 + lines 102/116)

```typescript
// alert/interfaces.d.ts
interface AlertProps {
  /**
   * Adds a close button to the alert when set to `true`.
   * An `onDismiss` event is fired when a user clicks the button.
   */
  dismissible?: boolean;   // optional; NO default value declared
  onDismiss?: NonCancelableEventHandler;
}
```

**Implementation evidence (`alert/internal.js` line 116):**
```javascript
// The dismiss button is rendered only when dismissible is truthy:
dismissible && React.createElement("div", { className: styles.dismiss }, ...)
```

**Conclusion:** `dismissible` has no declared default, so omitting it evaluates to `undefined` (falsy). The dismiss button is **not rendered**. Omitting `dismissible` yields a **non-dismissible** Alert. To make the `SimulatedDataBanner` permanently non-dismissible: pass neither `dismissible` nor `onDismiss`.

---

## (e) `BreadcrumbGroup` — Item Shape and `onFollow` Event Detail

**Sources:**
- `node_modules/@cloudscape-design/components/breadcrumb-group/interfaces.d.ts`
- `node_modules/@cloudscape-design/components/types/events.d.ts` (lines 20-25)

```typescript
// breadcrumb-group/interfaces.d.ts
interface BreadcrumbGroupProps.Item {
  text: string;
  href: string;
}

// BreadcrumbGroupProps.ClickDetail extends BaseNavigationDetail
interface ClickDetail<T extends Item = Item> extends BaseNavigationDetail {
  item: T;
  text: string;
  href: string;
}

// types/events.d.ts — BaseNavigationDetail
interface BaseNavigationDetail {
  href: string | undefined;
  external?: boolean;
  target?: string;
}

// onFollow fires only on plain left-click (no modifier keys)
onFollow?: CancelableEventHandler<BreadcrumbGroupProps.ClickDetail<T>>;
// Call event.preventDefault() inside onFollow to handle routing yourself.
```

**Usage pattern:**
```typescript
<BreadcrumbGroup
  items={[{ text: 'Home', href: '/' }, { text: 'Fleet Health', href: '/connectivity/fleet-health' }]}
  onFollow={(e) => { e.preventDefault(); navigate(e.detail.href); }}
/>
```

---

## (f) `Table.empty` Prop and Interaction with `useCollection` `filtering.empty` / `filtering.noMatch`

**Sources:**
- `node_modules/@cloudscape-design/components/table/interfaces.d.ts` (lines 28-30)
- `node_modules/@cloudscape-design/collection-hooks/mjs/interfaces.d.ts` (lines 31-35)
- `node_modules/@cloudscape-design/collection-hooks/mjs/utils.js` (lines 92-101)

### `Table.empty` prop

```typescript
// table/interfaces.d.ts
interface TableProps<T> {
  /**
   * Displayed when the `items` property is an empty array.
   * Use it to render an empty or no-match state.
   */
  empty?: React.ReactNode;
}
```

### How `useCollection` resolves which ReactNode goes into `collectionProps.empty`

From `utils.js` lines 92-101 (runtime implementation, not types):

```javascript
// allItems = the ORIGINAL array passed to useCollection() — NOT the filtered result
let empty = options.filtering
  ? allItems.length          // true when the original data set has items
    ? options.filtering.noMatch   // filter active, no matches → "No items match"
    : options.filtering.empty     // data set is empty → "No items to display"
  : null;
```

**Wiring pattern for two distinct empty states:**
```typescript
const { items, collectionProps, filterProps, paginationProps } = useCollection(allFleetItems, {
  filtering: {
    empty:   <TableEmptyState />,   // shown when allFleetItems.length === 0
    noMatch: <TableNoMatchState />, // shown when filter text matches nothing
    ...
  },
});

// Pass to Table:
<Table {...collectionProps} items={items} />
// collectionProps.empty is already the correct node for the current state.
```

**Two distinct components are required:** `TableEmptyState` (heading: "No vehicles") vs `TableNoMatchState` (heading: "No matches") tell the user whether to create data or clear the filter.

---

## (g) `TextFilter`, `Pagination`, and `CollectionPreferences` Prop Shapes

**Sources:**
- `node_modules/@cloudscape-design/components/text-filter/interfaces.d.ts`
- `node_modules/@cloudscape-design/components/pagination/interfaces.d.ts`
- `node_modules/@cloudscape-design/components/collection-preferences/interfaces.d.ts`

### `TextFilter`

```typescript
// text-filter/interfaces.d.ts — TextFilterProps
interface TextFilterProps {
  filteringText: string;                         // controlled value
  filteringPlaceholder?: string;
  filteringClearAriaLabel?: string;
  countText?: string;                            // e.g. "42 matches" — shown when filteringText non-empty
  loading?: boolean;
  disabled?: boolean;
  filteringAriaLabel?: string;
  onChange?: NonCancelableEventHandler<{ filteringText: string }>;
  onDelayedChange?: NonCancelableEventHandler<{ filteringText: string }>;
}
```

Spread `filterProps` from `useCollection` directly: `<TextFilter {...filterProps} />`. `filterProps` satisfies `{filteringText, onChange, disabled?}`.

### `Pagination`

```typescript
// pagination/interfaces.d.ts — PaginationProps
interface PaginationProps {
  currentPageIndex: number;   // 1-based
  pagesCount: number;
  openEnd?: boolean;          // lazy-load variant
  pagesVariant?: 'normal' | 'compact';
  disabled?: boolean;
  ariaLabels?: { nextPageLabel, previousPageLabel, pageLabel, paginationLabel };
  onChange?: NonCancelableEventHandler<{ currentPageIndex: number }>;
}
```

Spread `paginationProps` from `useCollection` directly: `<Pagination {...paginationProps} />`.

### `CollectionPreferences`

```typescript
// collection-preferences/interfaces.d.ts — CollectionPreferencesProps
interface CollectionPreferencesProps {
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  closeAriaLabel?: string;
  disabled?: boolean;
  pageSizePreference?: {
    title?: string;
    options: Array<{ value: number; label?: string }>;
  };
  contentDisplayPreference?: {          // recommended for tables (replaces visibleContentPreference)
    title?: string;
    description?: string;
    options: Array<{ id: string; label: string; alwaysVisible?: boolean }>;
    enableColumnFiltering?: boolean;
  };
  visibleContentPreference?: {          // for cards / deprecated for tables
    title: string;
    options: Array<{ label: string; options: Array<{ id: string; label: string; editable?: boolean }> }>;
  };
  wrapLinesPreference?: { label?: string; description?: string };
  stripedRowsPreference?: { label?: string; description?: string };
  contentDensityPreference?: { label?: string; description?: string };
  preferences?: CollectionPreferencesProps.Preferences;  // current values
  onConfirm?: NonCancelableEventHandler<Preferences>;
  onCancel?: NonCancelableEventHandler;
}

// Preferences shape (for controlled state):
interface Preferences {
  pageSize?: number;
  wrapLines?: boolean;
  stripedRows?: boolean;
  contentDensity?: 'comfortable' | 'compact';
  visibleContent?: ReadonlyArray<string>;
  contentDisplay?: ReadonlyArray<ContentDisplayItem>;
  custom?: any;
}
```

---

## (h) `Tabs` — Whether Tab Labels Render Inside `<nav>`

**Sources:**
- `node_modules/@cloudscape-design/components/tabs/tab-header-bar.js` (lines 244-254, 358-373)
- `node_modules/@cloudscape-design/components/tabs/index.js` (full file)

**Finding: Tab labels do NOT render inside a `<nav>` element.**

### DOM structure produced by `TabHeaderBar` (from `tab-header-bar.js` line 244-254):

```
<div className={classes}>                                          // outer wrapper — div
  <div className="tab-header-scroll-container">                   // scroll container — div
    <SingleTabStopNavigationProvider>
      <ul role="tablist"> | <div role="application">              // TabList — ul OR div
        <li role="presentation"> | <div role="presentation">      // TabItem — li OR div
          <div>                                                    // tabHeaderContainerClasses — div
            <a role="tab"> | <button role="tab">                  // TabTrigger — a or button
              {tab.label}                                          // ← TAB LABEL IS HERE
            </a>
          </div>
        </li>
      </ul>
    </SingleTabStopNavigationProvider>
  </div>
  {actions && <div className="actions-container">{actions}</div>}
</div>
```

**Key lines from `tab-header-bar.js`:**
```javascript
// Line 244: outer wrapper
const TabList = hasActionOrDismissible ? 'div' : 'ul';
// TabList renders as <ul role="tablist"> or <div role="application">

// Line 358: per-tab item
const TabItem = hasActionOrDismissible ? 'div' : 'li';
// TabItem renders as <li role="presentation"> or <div role="presentation">
```

No `<nav>` element appears at any level of the Tabs component tree. The word "nav" in `tab-header-bar.js` appears only as part of `navigationAPI` (an internal keyboard-navigation hook ref), not as an HTML element.

### Outer `Tabs` wrapper (`index.js`):

```javascript
// Non-container/stacked variant (the common case):
return React.createElement("div", { ...baseProps }, header, content());
// Root element is <div>, not <nav>.

// Container/stacked variant:
return React.createElement(InternalContainer, { header }, content());
// Still no <nav>.
```

### Implication for spec D6 (settleMarker strategy for tabbed screens)

**A `settleMarker` placed in a tab label WILL appear inside `<nav>` content IF the guard's
"strip nav" logic strips `role="tablist"` or the surrounding divs.**

The test infrastructure for the registry-completeness guard (T2.1) strips the side nav
(`<nav>` landmark or `SideNavigation` root). **It must not strip `role="tablist"` or the
surrounding divs** that Cloudscape's Tabs component uses, because those are not `<nav>` elements.

**Correct settleMarker placement for a tabbed screen:**
- Place the marker in the **tab content** (`tab.content`), not in `tab.label`.
  The content renders inside `<div role="tabpanel">` which is outside the header bar entirely.
- If the marker must be in the tab label for some reason, the P1 guard in T2.1 must
  **not** strip `role="tablist"` when removing nav content — it should only strip the
  `SideNavigation` element (the `<nav>` landmark), not the tabs header bar.

**Contrast with DMS's `AccountingView` assumption:** DMS relied on tab labels surviving
a nav-strip because it believed tab labels were inside `<nav>`. This belief was incorrect
for both `components@3.0.839` (DMS's version) and `3.0.1354` (this module's version). The
tab header bar has always been a `<div>` wrapper. DMS's guards happened to work because the
strip targeted `<nav>` landmarks (SideNavigation), and the Tabs component has no `<nav>`.
The conclusion is the same — tab labels survive the nav strip — but for the right reason:
**they were never inside `<nav>` to begin with**.

---

## Citation index

| Item | File path(s) |
|------|-------------|
| (a) `useCollection` | `node_modules/@cloudscape-design/collection-hooks/mjs/use-collection.d.ts` · `mjs/interfaces.d.ts` · `mjs/utils.js:92-101` · `mjs/operations/index.js` |
| (b) `AppLayout` | `node_modules/@cloudscape-design/components/app-layout/interfaces.d.ts` · `app-layout/defaults.d.ts` |
| (c) `SideNavigation` section | `node_modules/@cloudscape-design/components/side-navigation/interfaces.d.ts` |
| (d) `Alert` dismissible | `node_modules/@cloudscape-design/components/alert/interfaces.d.ts` · `alert/internal.js:116` |
| (e) `BreadcrumbGroup` | `node_modules/@cloudscape-design/components/breadcrumb-group/interfaces.d.ts` · `components/types/events.d.ts:20-25` |
| (f) `Table.empty` + `useCollection` filtering | `node_modules/@cloudscape-design/components/table/interfaces.d.ts:28-30` · `collection-hooks/mjs/interfaces.d.ts:31-35` · `collection-hooks/mjs/utils.js:92-101` |
| (g) `TextFilter` | `node_modules/@cloudscape-design/components/text-filter/interfaces.d.ts` |
| (g) `Pagination` | `node_modules/@cloudscape-design/components/pagination/interfaces.d.ts` |
| (g) `CollectionPreferences` | `node_modules/@cloudscape-design/components/collection-preferences/interfaces.d.ts` |
| (h) `Tabs` nav rendering | `node_modules/@cloudscape-design/components/tabs/tab-header-bar.js:244-254,358-373` · `tabs/index.js` |


---

# Part 2 — Cognito Hosted UI + PKCE authentication contract

Added by spec `2026-09-05-cms-connected-services-auth-integration` T1.1.

**Verification date: 2026-09-05.** Every claim below carries a doc URL, an `aws` CLI
invocation with its recorded output, or a `file:line` in a repo on this machine. Nothing here
is from model memory — the spec that motivated this section was itself wrong about the file it
was fixing (see § (n)), which is the reason for the citation discipline.

## (i) Pool and app-client configuration — recorded, not assumed

Invocation run 2026-09-05:

```
aws cognito-idp describe-user-pool-client \
  --user-pool-id <cms-staging-user-pool-id> \
  --client-id 6fig3o3ndv7a0j29irl0lm4qi5 \
  --region us-west-2 \
  --query 'UserPoolClient.[AllowedOAuthFlows,AllowedOAuthScopes,AllowedOAuthFlowsUserPoolClient,CallbackURLs,LogoutURLs,SupportedIdentityProviders]'
```

Output:

```json
[
  ["code", "implicit"],
  ["aws.cognito.signin.user.admin", "email", "openid", "phone", "profile"],
  true,
  ["https://staging.example.com/auth/callback",
   "https://dms.staging.example.com/auth/callback"],
  ["https://staging.example.com/",
   "https://dms.staging.example.com/"],
  ["AmazonFederate", "COGNITO"]
]
```

Consequences that gate this spec:

- `AllowedOAuthFlows` contains `code` → the authorization-code grant this spec builds is
  permitted. T1.1's stop condition (flows lacking `code`, or scopes lacking `openid`) is
  **not** tripped; `openid` is present.
- `AllowedOAuthFlowsUserPoolClient` is `true` → the Hosted UI endpoints are usable by this
  client.
- `CallbackURLs` does **not** contain a `cs.staging.example.com` entry. This is the
  live confirmation that the F4.1 registry line (T4.1) is a genuine prerequisite and not
  bookkeeping: the pool will reject the redirect until it is added.
- The client has **no** client secret in the flow this spec uses — PKCE is the substitute, which
  is why `code_challenge` is mandatory rather than optional here.

**Observation, deliberately out of scope:** `AllowedOAuthFlows` also contains `implicit`. The
implicit grant returns tokens in the URL fragment and AWS documents it as a legacy grant whose
tokens users can intercept and inspect
(<https://docs.aws.amazon.com/cognito/latest/developerguide/federation-endpoints-oauth-grants.html>).
Removing it would harden all three portals on this client, but it is shared with CMS and DMS and
so is not this spec's to change. Recorded here so it is not lost.

## (j) The `connected-services` group exists

```
aws cognito-idp get-group --user-pool-id <cms-staging-user-pool-id> \
  --group-name connected-services --region us-west-2
```

Returns the group. No creation task is needed. Its `Description` currently reads
"UI-only gate today (client-side React RequireAuth); server-side enforcement pending backend +
JWT integration" — after this spec the first clause is stale, but a group description is live
pool state rather than repo state and editing it is not in scope.

## (k) Federate-only in practice → no headless end-to-end test

`SupportedIdentityProviders` includes `COGNITO`, so password auth is *permitted* by the client,
but the CS portal's sign-in path is Federate Hosted UI and the demo personas on this pool
federate. An automated test cannot complete an interactive Federate sign-in.

**Testability boundary this imposes**, and it is a hard one:

| Segment | Automatable |
|---|---|
| unauthenticated → redirect to `/oauth2/authorize` with correct params | yes |
| Federate sign-in | **no — user only** |
| `/auth/callback?code=…&state=…` → token exchange → group check | yes, with a synthetic code and a stubbed token endpoint |
| the two joined, against the live pool | **no — user only** |

Every test in this spec therefore exercises one side or the other, never the join. Per spec § R1
no task may claim the flow works on unit evidence alone; the joined verdict is T5.3 user UAT.
This is the same shape as
`guidance-for-connected-vehicle-experience-on-aws/issues/2026-08-05-tier2-adp-read-path-never-exercised-live`
— a stub cannot fail the way a service fails.

## (l) `/oauth2/authorize` parameters

Source: <https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html>
(§ "Example: authorization code grant with PKCE").

The documented PKCE example request shape:

```
GET https://<domain>/oauth2/authorize
  ?response_type=code
  &client_id=<client-id>
  &redirect_uri=<callback>
  &state=<opaque>
  &scope=<space-separated>
  &code_challenge_method=S256
  &code_challenge=<base64url(SHA256(verifier))>
```

The doc states: "This request adds a `code_challenge` parameter. To complete the exchange of a
code for a token, you must include the `code_verifier` parameter in your request to the
`/oauth2/token` endpoint." The success response is a `302` carrying `code` and `state` back to
`redirect_uri`.

`state` is the CSRF control. It is opaque to Cognito — it is echoed back unmodified, so *the
client* is responsible for generating it unpredictably, storing it, and rejecting a callback
whose `state` is absent or does not match. Cognito does not do this for you.

## (m) `/oauth2/token` parameters

Source: <https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html>
(§ "Request parameters in body"). Body is `application/x-www-form-urlencoded`.

| Parameter | Per the doc |
|---|---|
| `grant_type` | Required. `authorization_code` for this flow. |
| `client_id` | "You must provide this parameter if the client is public and does not have a secret" — this client is public in this flow, so it is required. |
| `client_secret` | Not applicable — no secret in this flow. |
| `redirect_uri` | "Must be the same `redirect_uri` that was used to get `authorization_code`". A mismatch here is a common cause of an opaque token-endpoint failure. |
| `code` | The authorization code from the callback. |
| `code_verifier` | The PKCE verifier, per § (l). |

Also from that page: "The token endpoint returns a refresh token only when the `grant_type` is
`authorization_code`." So this flow — and only this flow — yields a `refresh_token`. See § (o)
for what this spec does and does not do with it.

## (n) Group membership travels in `cognito:groups`

Sources:
- <https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cognito-userpoolusertogroupattachment.html>
  — adding a user to a group "populates a `cognito:groups` claim to their access and identity
  tokens".
- <https://docs.aws.amazon.com/boto3/latest/reference/services/cognito-idp/client/create_group.html>
  — "Both ID and access tokens also contain a `cognito:groups` claim that list all the groups
  that a user is a member of."

So the authorization check reads `cognito:groups` from the decoded `id_token` payload and tests
for membership of `connected-services`. The claim is an array of group-name strings.

Two properties worth stating because they shape the implementation:

1. **The claim is absent, not empty, for a user in no groups.** Code must treat absent and
   `[]` identically — both are unauthorized. A `payload["cognito:groups"].includes(...)` on an
   absent claim throws, and a thrown error inside a guard that catches broadly can become an
   allow. Normalise to `[]` first, then test membership.
2. **Decoding is not verifying.** `atob`-splitting a JWT reads attacker-controllable bytes if
   the token did not come from the token endpoint over TLS. This portal's client-side group
   check is a *UI* control; the server-side control is the API Gateway Cognito authorizer that
   validates the signature. Nothing here should be described as authenticating the caller to a
   backend.

## (o) The DMS port source — files, commit, and what is deliberately left behind

Ported from repo `guidance-for-dealer-management-system-on-aws`, branch `main`,
commit `ffec018` (HEAD at 2026-09-05):

| DMS file | Lines | Taken |
|---|---|---|
| `frontend/src/auth/pkce.ts` | 32 | **yes, near-verbatim** — `base64urlEncode`, `randomBase64url`, `computeCodeChallenge` (Web Crypto only, no deps) |
| `frontend/src/auth/SimpleAuthProvider.tsx` | 520 | **adapted, partially** — see exclusions below |
| `frontend/src/auth/useAuth.ts` | 201 | adapted |
| `frontend/src/auth/RequireGroup.tsx` | 118 | pattern only; this module already has `RequireAuth` + `Unauthorized` |
| `frontend/src/auth/useEmulatedRole.tsx` | 125 | **excluded** |
| `frontend/src/auth/useUserRole.ts` | 177 | **excluded** |

Kept because they are correct and non-obvious:

- Single-use `state`: read then `removeItem` before use (`SimpleAuthProvider.tsx:306-307`).
- Single-use `code_verifier`: same pattern (`:315-316`).
- Open-redirect guard on the post-login return path — `preAuthUrl.startsWith('/') &&
  !preAuthUrl.startsWith('//')`, else fall back to `/` (`:346`). The `//` case is what makes
  this a real guard rather than a cosmetic one: `//evil.example` is a protocol-relative
  absolute URL that passes a naive `startsWith('/')`.
- 32 random bytes → 43 base64url characters, inside RFC 7636's 43–128 range (`:363`).

**Excluded, each for a stated reason:**

| Excluded | Why |
|---|---|
| `LoginForm` + `login()` password path (`:37-207`, `:384-431`) | Uses `CognitoIdentityProviderClient` from `@aws-sdk/client-cognito-identity-provider` — a **new runtime dependency**, forbidden by spec § D1/Constraints. Also unnecessary: this portal's path is Hosted UI. |
| `useEmulatedRole` / `useUserRole` | Role emulation is a DMS product feature. Porting a mechanism that synthesises a caller's authorization from client-side state into *this* module would reintroduce the exact bypass class this spec deletes. |
| `localStorage` token persistence + "remember me" (`:223`, `:423-426`) | Spec § Design puts tokens in `sessionStorage`. `localStorage` survives tab close and widens the theft window for no benefit on an internal portal. |
| `COGNITO_DOMAIN = … \|\| import.meta.env.VITE_COGNITO_DOMAIN \|\| ''` (`:292-294`) | The `\|\| ''` falsy-coalesce is precisely what spec T3.4 forbids. An empty domain yields a redirect to `https:///oauth2/authorize`, which fails opaquely. This module routes all config through `env.ts:getRuntimeConfig()`, which already throws on absent config. |
| `REDIRECT_URI = import.meta.env.VITE_REDIRECT_URI \|\| …` (`:295`) | Same class. A build-time env var that can silently override the callback origin is a config-mistake surface; derive from `runtimeConfig.callbackOrigin`. |

### Correction to the DMS issue report (recorded so it is not re-inherited)

`guidance-for-dealer-management-system-on-aws/issues/2026-09-04-dms-ui-no-token-refresh`
states its "only expiry check runs once on mount". Read against the tree at `ffec018`, that is
**inaccurate**: `SimpleAuthProvider.tsx:270-290` registers `setInterval(checkTokenExpiration,
60000)` which calls `logout()` on an expired `exp`. There are two checks, not one.

The real defect is narrower and worth naming precisely, because the fix differs: the token
exchange at `:336-341` stores `access_token` and `id_token` and **silently discards
`refresh_token`**, which § (m) confirms this grant returns. So the session cannot be renewed and
dies at TTL; and because the sweep is on a 60-second interval, there is a window of up to 60s in
which an expired token is still presented and the API 401s.

What this spec does about it:

- **In scope** — expiry is treated as no-session and fails closed to the authorize redirect. That
  is not an added feature; it is spec § D3 property 1 applied to an expired token. A rendered
  app shell that 401s on every call is the empty-render defect D3 rejects, wearing a different
  hat.
- **Out of scope** — actually exchanging `refresh_token` for a new `id_token`. That is a feature
  beyond D3 and belongs in a follow-on filed against both portals together, since the defect is
  DMS's and the pattern is shared.

## (p) Citation index — Part 2

| Item | Source |
|---|---|
| (i) client config | `aws cognito-idp describe-user-pool-client`, run 2026-09-05, output inline |
| (i) implicit grant is legacy | <https://docs.aws.amazon.com/cognito/latest/developerguide/federation-endpoints-oauth-grants.html> |
| (j) group exists | `aws cognito-idp get-group`, run 2026-09-05 |
| (l) authorize endpoint | <https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html> |
| (m) token endpoint | <https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html> |
| (n) `cognito:groups` | <https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cognito-userpoolusertogroupattachment.html> · <https://docs.aws.amazon.com/boto3/latest/reference/services/cognito-idp/client/create_group.html> |
| (o) port source | `guidance-for-dealer-management-system-on-aws@ffec018` `frontend/src/auth/*` with `file:line` inline |
