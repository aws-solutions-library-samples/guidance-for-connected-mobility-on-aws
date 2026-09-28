# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runtime modules shared across CMS services.

Source-of-truth for code that more than one deployment unit must execute. Modules
here are overlaid into each consumer's bundle at CDK synth time, the same mechanism
`_lib/` uses — see `deployment/stacks/commands_stack.py`.

**Do not copy anything out of this package into a consumer.** The reason this package
exists is F28: `routine_catalog` lived in `deployment/scripts/`, which is bundled into
no runtime, so neither the commands Lambda nor the vehicle-ECU sidecar could consult
the authority that assigns a routine's safety class. Both read the class out of the
request instead, and the sidecar defaulted a missing value to `INERT` — the least
restricted class — so omitting one field ran a `SERVICE_ONLY` diesel routine on a
moving vehicle.

A safety authority that ships in the test tree and not in the runtime is not an
authority. If you add a module here, add it to every consumer's staging overlay in the
same change, and assert at synth that it is present in the bundle — an import that
works locally proves nothing about what ships.
"""
