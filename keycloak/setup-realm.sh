#!/usr/bin/env bash
set -euo pipefail

KEYCLOAK_URL=${KEYCLOAK_URL:-http://localhost:8080}
ADMIN_USER=${KEYCLOAK_ADMIN:-admin}
ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD:-admin}
REALM=demo-client

KCADM="/opt/keycloak/bin/kcadm.sh"

$KCADM config credentials --server "$KEYCLOAK_URL" --realm master --user "$ADMIN_USER" --password "$ADMIN_PASSWORD"
$KCADM create authentication/flows -r "$REALM" -s alias=legacy-browser -s providerId=basic-flow -s topLevel=true -s builtIn=false || true
$KCADM create authentication/flows/legacy-browser/executions/execution -r "$REALM" -s provider=rest-authenticator || true

EXEC_ID=$($KCADM get authentication/flows/legacy-browser/executions -r "$REALM" --format csv --fields id,providerId | awk -F, '$2=="rest-authenticator" {print $1; exit}')

if [[ -n "$EXEC_ID" ]]; then
  $KCADM update "authentication/executions/$EXEC_ID" -r "$REALM" -s requirement=REQUIRED
  $KCADM create "authentication/executions/$EXEC_ID/config" -r "$REALM" \
    -s alias=legacy-rest-config \
    -s config.verify_url=http://client-app:3000/auth/verify \
    -s config.api_key=demo-shared-api-key || true
fi

$KCADM update realms/$REALM -s browserFlow=legacy-browser

echo "Realm $REALM configured with rest-authenticator browser flow"
