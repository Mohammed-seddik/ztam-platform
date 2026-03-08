# Demo Assets

This directory contains the bundled sample application and bootstrap tooling used for local demonstrations of the ZTAM platform.

Contents:

- `testapp/`: sample protected Node.js application and its frontend assets
- `setup_demo.py`: one-time Keycloak bootstrap and smoke-test script for the sample environment
- `demo_test.sh`: quick end-to-end demo helper against the bundled sample stack

Platform code remains at the repo root under `services/`, `policies/`, `envoy/`, `scripts/`, and `tenants/`.

Compatibility note:

- Root-level `setup_demo.py` and `demo_test.sh` wrappers remain available so older commands still work.
