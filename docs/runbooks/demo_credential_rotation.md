# Demo Credential Rotation Runbook

This runbook covers rotating demo persona passwords and managing demo persona group membership.

## Prerequisites

- AWS CLI v2 configured with credentials for the target stage (staging / prod)
- Python 3.x with boto3
- `connected-mobility-guidance-on-aws` repo checked out

## How the Quick-Login Buttons Work

The quick-login buttons on the staging login page are one-click sign-in:

1. Each button corresponds to a demo persona (Fleet Manager, Agent, Engineer, Dispatcher)
2. When clicked, the button reads `runtimeConfig.demoPasswords[email]` from the frontend config
3. If the password is present (staging, after `make regenerate-runtime-config`), the browser submits it via `cognito-idp:InitiateAuth` → one-click sign-in
4. If the password is absent (prod, or staging before regenerate), the button falls back to prefill-email-only (no sign-in attempted) — this is the degrade-safe path

**Intermediate state**: After `make staging-deploy` but before `make regenerate-runtime-config`, the buttons render but do not submit a password — the email is prefilled and the password field shows the browser's "paste here" hint. This is expected and is the defined degrade-safe behavior.

**If a button falls back to prefill-email-only**: Run `make regenerate-runtime-config` to inject the passwords from Secrets Manager into `runtimeConfig.json` and redeploy to CloudFront.

## Auto-Sign-In on Landing (Group E, 2026-08-10)

When `runtimeConfig.demoPasswords` is present (staging, after `make regenerate-runtime-config`), navigating to the staging app auto-signs you in without clicking a button:

- **Default persona**: `FleetManager@example.com` (unless `localStorage['cms.lastPersona']` is set to a different persona in `demoPasswords`)
- **Opt out for the current tab**: Click "Sign out" in the top-nav user menu. The flag `sessionStorage['cms.suppressAutoSignIn']` is set so auto-sign-in is skipped for the tab's lifetime. Reopening the tab clears the flag.
- **Loading state**: A "Signing in…" spinner is shown rather than flashing the login form.
- **Prod**: auto-sign-in never triggers (demoPasswords absent).

## Persona Switcher (Fix Group E, 2026-08-10 — account dropdown)

After auto-sign-in (or manual sign-in) on staging, persona items appear at the TOP of the
account dropdown (the user icon / email button in the top-nav header):

- Shows 🚛 Fleet Manager, 🎧 Agent, 🔬 Product Engineer, 📋 Dispatcher at the top, followed by the existing Preferences/Support/Sign out items
- Current persona is shown as disabled with "(current)" appended to its label
- Click any other persona to switch instantly — reads the password from `runtimeConfig.demoPasswords` at click time, never stores it
- Writes `cms.lastPersona` to localStorage so the next auto-sign-in lands on the last-used persona
- Prod: `demoPasswords` absent → persona items are absent from the dropdown entirely

**Note**: The original Group E implementation used a separate overlay icon (three vertical dots) in the header. Fix Group E moved the items into the existing account dropdown for a cleaner UX.

**If a persona switch fails**: The current session is preserved. Re-run `make regenerate-runtime-config` if passwords may have been rotated since the page was loaded.

## Rotate All Demo Passwords (Staging)

Use this procedure to generate new passwords for all four demo personas without a CDK redeploy.

```bash
cd deployment

# Dry-run first — see what will change
python3 scripts/rotate_demo_login.py --stage staging

# Apply the rotation
python3 scripts/rotate_demo_login.py --stage staging --apply

# Verify the change
aws cognito-idp admin-get-user \
  --user-pool-id <user-pool-id> \
  --username "FleetManager@example.com" \
  --region us-west-2 \
  | jq '.UserLastModifiedDate'
```

The `UserLastModifiedDate` should show the current time (or very recent). If it shows an old date, the rotation did not apply — check CloudWatch logs for the script or run with `--apply` again.

## Rotate One Persona's Password

Rotate a single persona (e.g., if that account was compromised):

```bash
# Auto-generate a new password
python3 scripts/rotate_demo_login.py \
  --stage staging \
  --user agent1@cms-fleet.io \
  --apply

# Or supply your own password (NOT recommended for production)
export CMS_DEMO_NEW_PASSWORD='YourNewPassword@2026'
python3 scripts/rotate_demo_login.py \
  --stage staging \
  --user engineer@example.com \
  --apply
unset CMS_DEMO_NEW_PASSWORD
```

The script reads passwords only from Secrets Manager or the `CMS_DEMO_NEW_PASSWORD` environment variable — never from the command line. After rotation, the next `make regenerate-runtime-config` will apply the new password to `runtimeConfig.json`.

## Fix Demo Persona Group Membership (Staging)

If demo personas are assigned to the wrong groups (or accidentally to `platform-admin`), use the deprivilege script to reset them to the correct minimum groups:

```bash
cd deployment

# Dry-run: see the intended changes
python3 scripts/deprivilege_demo_personas.py --stage staging --diff

# Apply the fixes
python3 scripts/deprivilege_demo_personas.py --stage staging --apply

# Verify each persona
for email in "FleetManager@example.com" "agent1@cms-fleet.io" "engineer@example.com" "kevin.dispatch@example.com"; do
  echo "=== $email ==="
  aws cognito-idp admin-list-groups-for-user \
    --user-pool-id <user-pool-id> \
    --username "$email" \
    --region us-west-2 \
    --query 'Groups[].GroupName' --output table
done
```

**Expected output**:
- FleetManager@example.com: `fleet-operator` (NOT `platform-admin`)
- agent1@cms-fleet.io: `connect-agent` (or `agent`)
- engineer@example.com: `product-engineer`
- kevin.dispatch@example.com: `dispatcher`

**Prod group changes** are hard-refused by the script. Prod demo credentials and groups are managed manually or via a separate spec.

## Retrieve a Persona Password (Manual Sign-In)

If the one-click buttons are not working or you need to sign in manually:

```bash
# Set the stage and region
DEPLOYMENT_STAGE=staging
REGION=us-west-2

# Retrieve FleetManager@example.com password
aws secretsmanager get-secret-value \
  --secret-id "cms-${DEPLOYMENT_STAGE}-demo-user-password" \
  --region "$REGION" \
  --query 'SecretString' \
  --output text

# Retrieve agent1, engineer, dispatcher passwords (JSON object keyed by email)
aws secretsmanager get-secret-value \
  --secret-id "cms-${DEPLOYMENT_STAGE}-demo-persona-passwords" \
  --region "$REGION" \
  --query 'SecretString' \
  --output text | jq -r '.["agent1@cms-fleet.io"]'
```

These commands return the plaintext password. Use them only in a secure context (never share in chat, logs, or screenshots); rotation is the remediation if a password is exposed.

## Troubleshooting

### "secret not found" error

If `rotate_demo_login.py` returns `ResourceNotFoundException`:

1. Check the secret was created by the initial CDK deploy:
   ```bash
   aws secretsmanager list-secrets \
     --region us-west-2 \
     --filter Key=name,Values=demo
   ```
   Should list `cms-staging-demo-user-password` and `cms-staging-demo-persona-passwords`.

2. If missing, redeploy to create them:
   ```bash
   DEPLOYMENT_STAGE=staging make staging-deploy
   ```

3. If the secrets exist but the script still fails, check your AWS credentials:
   ```bash
   aws sts get-caller-identity
   ```

### "UserNotFoundException" when rotating

If the script reports that a persona account doesn't exist:

1. Verify the account is seeded in the Cognito pool:
   ```bash
   aws cognito-idp admin-get-user \
     --user-pool-id <user-pool-id> \
     --username "agent1@cms-fleet.io" \
     --region us-west-2
   ```

2. If not found, seed it via the deployment scripts:
   ```bash
   DEPLOYMENT_STAGE=staging make seed-personas
   ```

3. Re-run the rotation.

### Persona still has `platform-admin` after deprivilege

1. Run the deprivilege dry-run again to confirm the intended change:
   ```bash
   python3 scripts/deprivilege_demo_personas.py --stage staging --diff
   ```

2. Apply with `--apply`:
   ```bash
   python3 scripts/deprivilege_demo_personas.py --stage staging --apply
   ```

3. Verify with the `admin-list-groups-for-user` check above.

4. If still wrong, manually remove the group:
   ```bash
   aws cognito-idp admin-remove-user-from-group \
     --user-pool-id <user-pool-id> \
     --username "FleetManager@example.com" \
     --group-name platform-admin \
     --region us-west-2
   ```

### DEPLOYMENT_STAGE unset error

If synth or deploy fails with "unrecognised stage" or "DEPLOYMENT_STAGE not set":

```bash
export DEPLOYMENT_STAGE=staging  # or prod
make staging-deploy
```

All CMS deployment scripts require `DEPLOYMENT_STAGE` to be set explicitly — they do NOT default to staging. This is by design, to prevent accidental prod changes.

### Buttons fall back to prefill-email-only after deploy

Run `make regenerate-runtime-config` to inject the demo passwords from Secrets Manager into the live `runtimeConfig.json`:

```bash
DEPLOYMENT_STAGE=staging make regenerate-runtime-config
```

This reads the four persona passwords from Secrets Manager and writes them into the S3-hosted `runtimeConfig.json`. After ~60-120 seconds for CloudFront invalidation, the buttons will work one-click.

## Related Procedures

- **Deploy a fresh staging environment**: Run `make staging-deploy` — this automatically creates secrets and seeds demo personas.
- **Rotate in prod**: NOT automated. Prod credential changes require explicit user authorization and manual Cognito operations (see spec `.kiro/specs/2026-08-05-cms-demo-identity-model/decisions.md` § "STOP GATE: prod authorization change").
