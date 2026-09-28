// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RequireAuth — fail-closed UI route guard.
 *
 * Decides what to RENDER. Not an authorization boundary: the group check runs in the
 * browser against an unverified token. Backend endpoints must enforce their own
 * server-side check — see the warning in src/auth.ts's header.
 *
 * Spec T9.1 (supersedes T3.1 for the unauthenticated case):
 *   - If no valid session exists (absent, expired, or malformed id-token):
 *     renders <SignIn /> so the user can initiate the Federate redirect.
 *   - If a valid session exists but lacks the "connected-services" group:
 *     renders <Unauthorized /> — explicit error, never empty.
 *   - If authorized: renders children.
 *
 * The "never empty" invariant from T3.1 still holds — both SignIn and
 * Unauthorized are always non-empty components.
 *
 * Fail-closed: null session → SignIn; wrong group → Unauthorized.
 * An empty render is still a defect, not a pass.
 */

import React from "react";
import { getSession, isAuthorized } from "../auth";
import SignIn from "./SignIn";
import Unauthorized from "./Unauthorized";

interface RequireAuthProps {
  children: React.ReactNode;
}

/**
 * Fail-closed route guard.
 *
 * - No session (absent/expired/malformed)  → <SignIn />
 * - Session without connected-services     → <Unauthorized />
 * - Session with connected-services        → children
 *
 * Never renders empty. The guard calls getSession() synchronously on every
 * render; sessionStorage reads are synchronous so no loading state is needed.
 */
const RequireAuth: React.FC<RequireAuthProps> = ({ children }) => {
  const session = getSession();

  // No valid session — offer the sign-in screen
  if (session === null) {
    return <SignIn />;
  }

  // Session exists but lacks the required group — explicit unauthorized
  if (!isAuthorized(session)) {
    return <Unauthorized />;
  }

  return <>{children}</>;
};

export default RequireAuth;
