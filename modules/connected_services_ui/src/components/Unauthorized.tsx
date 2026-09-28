// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Unauthorized — fail-closed authorization component.
 *
 * Spec T3.1: "Fail-closed is the requirement — a route that renders empty for
 * an unauthorized persona is a defect, not a pass."
 *
 * This component renders an explicit, user-visible unauthorized state rather
 * than an empty page.  It is rendered by RequireAuth when the current session
 * lacks the "connected-services" Cognito group.
 */

import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import React from "react";

interface UnauthorizedProps {
  /** Optional message shown under the alert.  Defaults to a standard explanation. */
  detail?: string;
}

/**
 * Render an explicit unauthorized state.
 *
 * NEVER renders empty.  If the session check is wrong and this component is
 * reached by a legitimate user, the component is still visible and actionable —
 * the user knows something went wrong rather than seeing a blank page.
 */
const Unauthorized: React.FC<UnauthorizedProps> = ({
  detail = "Your account does not have access to the Connected Services portal. " +
    "Contact your administrator to request the 'connected-services' Cognito group.",
}) => {
  return (
    <Box padding="xxl" data-testid="unauthorized-view">
      <Alert
        type="error"
        header="Unauthorized — Connected Services Portal"
        data-testid="unauthorized-alert"
      >
        {detail}
      </Alert>
    </Box>
  );
};

export default Unauthorized;
