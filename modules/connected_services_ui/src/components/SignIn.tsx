// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignIn — unauthenticated landing screen for the Connected Services portal.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/ T9.1
 *
 * Renders an email + password form (primary path, works without a registered
 * callback origin) and a "Sign in with Amazon (Federate)" button below it.
 * Config is read only from getRuntimeConfig() — nothing is baked into the bundle.
 *
 * Username/password path mirrors CMS SimpleAuthProvider.tsx `login()`:
 *   - USER_PASSWORD_AUTH via signInWithPassword() in auth.ts
 *   - Stores authToken + idToken to sessionStorage (same keys as the Federate path)
 *   - After storing tokens, reloads the page so RequireAuth re-evaluates the session
 *
 * Federate path: unchanged from T9.1 — redirects to Hosted UI authorize endpoint
 * with PKCE + state. Button keeps data-testid="federate-sign-in-button".
 */

import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Form from "@cloudscape-design/components/form";
import React, { useRef, useState } from "react";
import { getRuntimeConfig } from "../env";
import { redirectToFederate, signInWithPassword } from "../auth";

/**
 * Sign-in screen rendered by RequireAuth when no valid session exists.
 *
 * Order of controls:
 *   1. Email + Password form (submit button — primary path, no callback needed)
 *   2. Divider
 *   3. "Sign in with Amazon (Federate)" button (secondary path)
 *   4. Simulated-data notice
 */
const SignIn: React.FC = () => {
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  // Guards against a second in-flight credential submission. `loading` state cannot do
  // this alone: two handler invocations dispatched in the same React batch both read the
  // pre-update `loading === false`. A ref is written synchronously, so the second
  // invocation sees it. Without this, one click issued two InitiateAuth calls to Cognito.
  const submittingRef = useRef(false);

  const isSubmitDisabled = email.trim() === "" || password === "" || loading;

  const handlePasswordSignIn = (e?: React.FormEvent): void => {
    if (e) e.preventDefault();
    if (isSubmitDisabled || submittingRef.current) return;
    submittingRef.current = true;
    setError(null);
    setLoading(true);
    let config;
    try {
      config = getRuntimeConfig();
    } catch {
      setError("Runtime configuration is not available. Please contact support.");
      setLoading(false);
      submittingRef.current = false;
      return;
    }
    void signInWithPassword(email, password, config)
      .then((result) => {
        if (result.ok) {
          // Tokens stored; reload so RequireAuth re-evaluates the session.
          window.location.reload();
        } else {
          setError(result.message);
          setLoading(false);
          submittingRef.current = false;
        }
      })
      .catch(() => {
        setError("Sign-in failed. Please try again.");
        setLoading(false);
        submittingRef.current = false;
      });
  };

  const handleFederateSignIn = (): void => {
    setError(null);
    try {
      const config = getRuntimeConfig();
      void redirectToFederate(config).catch(() => {
        setError(
          "Could not start sign-in. Refresh the page and try again; if it persists, " +
            "contact support."
        );
      });
    } catch {
      setError("Runtime configuration is not available. Please contact support.");
    }
  };

  return (
    // Centred card on the layout background — same shape as CMS/DMS
    // `SimpleAuthProvider`'s LoginForm: full-viewport flex centring, a Cloudscape
    // Container as the card, and a bounded column inside it. The previous version was a
    // bare `Box padding="xxl" textAlign="center"` with no Container, so it read as
    // unstyled page text rather than a Cloudscape sign-in surface.
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        // Design token with a literal fallback. The token name is NOT hashed (it is a
        // published token, unlike the per-build layout custom properties this module was
        // bitten by earlier), so it resolves across Cloudscape versions.
        backgroundColor: "var(--color-background-layout-main-5ilwcb, #f2f3f3)",
      }}
      data-testid="sign-in-view"
    >
      <Container>
        <div style={{ width: "400px", maxWidth: "100%", margin: "0 auto" }}>
          <SpaceBetween size="l" direction="vertical">
            <Header variant="h1" description="Sign in to access this portal.">
              Connected Services Portal
            </Header>

            {error !== null && (
              <Alert type="error" data-testid="sign-in-error">
                {error}
              </Alert>
            )}

            {/* Native <form> so Enter submits and the browser treats these as
                credential fields. Cloudscape's Form is layout only — it does not
                accept onSubmit — so the native element wraps it. */}
            <form onSubmit={handlePasswordSignIn} data-testid="password-sign-in-form">
              <Form>
                <SpaceBetween size="m" direction="vertical">
                  <FormField label="Email">
                    <Input
                      type="email"
                      value={email}
                      onChange={({ detail }) => setEmail(detail.value)}
                      placeholder="Enter your email"
                      disabled={loading}
                      autoComplete="email"
                      autoFocus
                      data-testid="email-input"
                    />
                  </FormField>

                  <FormField label="Password">
                    <Input
                      type="password"
                      value={password}
                      onChange={({ detail }) => setPassword(detail.value)}
                      placeholder="Enter your password"
                      disabled={loading}
                      autoComplete="current-password"
                      data-testid="password-input"
                    />
                  </FormField>

                  <Button
                    variant="primary"
                    loading={loading}
                    disabled={isSubmitDisabled}
                    fullWidth
                    // No onClick. Cloudscape Button defaults to formAction="submit", which
                    // renders type="submit", so the enclosing form's onSubmit is the single
                    // submission path — one call whether the user clicks or presses Enter.
                    // An onClick here would fire IN ADDITION to onSubmit, issuing two
                    // InitiateAuth calls to Cognito per click.
                    data-testid="password-sign-in-button"
                  >
                    Sign in
                  </Button>

                  {/* Divider */}
                  <Box textAlign="center" color="text-body-secondary" variant="small">
                    — or —
                  </Box>

                  <Button
                    variant="normal"
                    onClick={handleFederateSignIn}
                    fullWidth
                    // Outside the submit path: this must not submit the credential form.
                    formAction="none"
                    data-testid="federate-sign-in-button"
                  >
                    Sign in with Amazon (Federate)
                  </Button>
                </SpaceBetween>
              </Form>
            </form>

            {/* The one place this statement appears in full. It used to render on all 23
                screens via SimulatedDataBanner; a compact top-bar indicator carries it
                inside the app now. See decisions.md 2026-09-05. */}
            <Alert type="info" data-testid="simulated-data-notice">
              All data in this portal is simulated fixture data. No live API calls are made
              in this release.
            </Alert>
          </SpaceBetween>
        </div>
      </Container>
    </div>
  );
};

export default SignIn;
