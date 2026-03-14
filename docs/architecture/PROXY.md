# Envoy Proxy — How It Is Used in ZTAM

This document explains how the Envoy reverse proxy is configured and used as the public
gateway in the ZTAM platform.

---

## Role

Envoy acts as the **Policy Enforcement Point (PEP)** for the entire platform.

Every byte of traffic from a browser passes through Envoy. No backend service is directly
reachable from outside the Docker network. Envoy is the only container with public-facing
ports.

---

## Listeners

### HTTP listener — port 80

All plain HTTP requests receive an HTTP 301 redirect to the HTTPS equivalent URL.

```yaml
- name: http_listener
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 80
  filter_chains:
    - filters:
        - name: envoy.filters.network.http_connection_manager
          typed_config:
            route_config:
              virtual_hosts:
                - name: local_redirect
                  domains: ["*"]
                  routes:
                    - match: { prefix: "/" }
                      redirect:
                        https_redirect: true
```

### HTTPS listener — port 443

All protected traffic enters through this listener with TLS termination.

Envoy reads the TLS certificate and private key from `envoy/certs/` (generated locally
by `envoy/generate-certs.sh`).

```yaml
- name: https_listener
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 443
  filter_chains:
    - transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          common_tls_context:
            tls_certificates:
              - certificate_chain: { filename: "/etc/envoy/certs/localhost.crt" }
                private_key:       { filename: "/etc/envoy/certs/localhost.key" }
```

---

## Header Stripping

Before matching any routes, Envoy removes incoming identity headers so clients cannot
inject or spoof trust context:

```yaml
request_headers_to_remove:
  - x-user-id
  - x-user-roles
  - x-user-email
  - x-tenant-id
  - x-ztam-jwt
```

These headers are only ever set by the auth-middleware after a successful authorization
check and are then injected by Envoy into the forwarded upstream request.

---

## External Authorization (`ext_authz`)

The HTTPS listener enables the `envoy.filters.http.ext_authz` HTTP filter globally:

```yaml
http_filters:
  - name: envoy.filters.http.ext_authz
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
      grpc_service:
        envoy_grpc:
          cluster_name: auth_middleware_cluster
      transport_api_version: V3
      status_on_error:
        code: ServiceUnavailable
      with_request_body:
        max_request_bytes: 8192
        allow_partial_message: true
      include_peer_certificate: true
```

**What this means in practice:**

- For every request that does **not** have `ext_authz` explicitly disabled on its route,
  Envoy pauses and sends the full request headers (path, method, `Authorization`) to
  the auth-middleware over gRPC.
- If auth-middleware returns `200 OK`, Envoy forwards the request to the upstream backend
  and injects any response headers from auth-middleware (the trusted identity headers).
- If auth-middleware returns `401` or `403`, Envoy immediately returns that response to
  the browser without touching the backend.
- **`status_on_error: ServiceUnavailable`** — if auth-middleware is unreachable, Envoy
  returns `503`. There is no `failure_mode_allow` fallback; the default is to deny on
  error.

---

## Tenant Virtual Hosts and Routing

Each tenant is assigned a **virtual host** inside the HTTPS listener, identified by its
`domains` list (the FQDN configured in `tenants/<name>/config.json`).

Envoy matches the incoming `Host` header against these domains and applies the correct
route table for that tenant.

### Route priority within a tenant virtual host

Routes are evaluated top-to-bottom. The first match wins.

| Match pattern           | Upstream cluster        | `ext_authz`         | Notes                                          |
|-------------------------|-------------------------|---------------------|------------------------------------------------|
| `prefix: /ztam/`        | `auth_middleware_cluster` | **disabled**       | ZTAM platform login pages, served directly     |
| `path: /api/auth/login` | `auth_middleware_cluster` | **disabled**       | Rewritten to `/login-proxy` on auth-middleware |
| `path: /api/auth/logout`| `auth_middleware_cluster` | **disabled**       | Rewritten to `/logout` on auth-middleware      |
| `prefix: /static/`      | `<tenant>_cluster`      | **disabled**        | Public static assets                           |
| `prefix: /`             | `<tenant>_cluster`      | **ENABLED** (default) | All other requests — enforced               |

The catch-all `prefix: /` route at the bottom has `ext_authz` enabled because no
`typed_per_filter_config` override is present. All routes that must bypass auth
explicitly add:

```yaml
typed_per_filter_config:
  envoy.filters.http.ext_authz:
    "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
    disabled: true
```

### Internal auth secret

For routes forwarded to auth-middleware, Envoy injects a shared secret header so that
auth-middleware can reject requests that did not originate from Envoy:

```yaml
request_headers_to_add:
  - append_action: OVERWRITE_IF_EXISTS_OR_ADD
    header:
      key: x-ztam-internal-auth
      value: "%ENVIRONMENT(ENVOY_AUTH_SHARED_SECRET)%"
```

The value is read from the `ENVOY_AUTH_SHARED_SECRET` environment variable at runtime.
Auth-middleware validates this header on every incoming call and rejects requests that
omit or mismatch it.

---

## Security Response Headers

Every tenant virtual host adds the following security headers to all responses:

| Header                      | Value                                                                                         |
|-----------------------------|-----------------------------------------------------------------------------------------------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload`                                                |
| `X-Content-Type-Options`    | `nosniff`                                                                                     |
| `X-Frame-Options`           | `SAMEORIGIN`                                                                                  |
| `Referrer-Policy`           | `strict-origin-when-cross-origin`                                                             |
| `Content-Security-Policy`   | `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'` |
| `Permissions-Policy`        | `geolocation=(), microphone=(), camera=(), payment=()`                                        |
| `Cache-Control`             | `no-store`                                                                                    |

These are added unconditionally at the virtual-host level, regardless of the route.

---

## Cluster Definitions

Envoy defines an upstream cluster for each backend service.

### `auth_middleware_cluster`

```yaml
- name: auth_middleware_cluster
  type: STRICT_DNS
  connect_timeout: 0.25s
  http2_protocol_options: {}
  load_assignment:
    endpoints:
      - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: auth-middleware
                  port_value: 8001
```

HTTP/2 is enabled because `ext_authz` communicates over gRPC, which requires HTTP/2.

### Tenant clusters

Each tenant gets its own cluster generated by `tenant_manager.py`:

```yaml
- name: testapp_cluster
  connect_timeout: 10s
  type: LOGICAL_DNS
  dns_lookup_family: V4_ONLY
  load_assignment:
    endpoints:
      - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: testapp
                  port_value: 3000
```

The `address` and `port_value` come from the `backend_host` and `backend_port` fields
in the tenant config.

---

## Admin Interface

```yaml
admin:
  access_log_path: /tmp/admin_access.log
  address:
    socket_address:
      address: 127.0.0.1
      port_value: 9901
```

The Envoy admin API is bound to `127.0.0.1` only (loopback). It is not reachable from
outside the container or from the Docker network, preventing any external reconfiguration
of the proxy.

---

## Configuration Generation

The virtual host blocks and cluster blocks inside `envoy.yaml` are **generated output**,
not hand-edited.

`scripts/tenant_manager.py` reads `tenants/<name>/config.json` files and injects the
correct YAML blocks between the marker comments:

```
# __ZTAM_TENANT_ROUTES_BEGIN__
...generated virtual host blocks...
# __ZTAM_TENANT_ROUTES_END__

# __ZTAM_TENANT_CLUSTERS_BEGIN__
...generated cluster blocks...
# __ZTAM_TENANT_CLUSTERS_END__
```

To add or remove a tenant from the Envoy config, run:

```bash
# Add a tenant
python3 scripts/tenant_manager.py apply

# Remove a tenant
python3 scripts/offboard-tenant.sh --name <tenant-name>
```

Do not manually edit the sections between the `BEGIN` / `END` markers — they will be
overwritten on the next apply.

---

## Summary

| Concern                      | How Envoy handles it                                                           |
|------------------------------|--------------------------------------------------------------------------------|
| TLS termination              | Downstream TLS context with cert/key from `envoy/certs/`                      |
| HTTP → HTTPS upgrade         | HTTP listener returns 301 redirect                                             |
| Authentication enforcement   | `ext_authz` filter pauses requests and delegates to auth-middleware            |
| Authorization bypass         | Explicit `disabled: true` per route for public paths                           |
| Identity header injection    | Auth-middleware response headers forwarded to upstream by Envoy                |
| Identity header spoofing     | Stripped at connection ingress before route matching                           |
| Security headers             | Applied at virtual-host level on all responses                                 |
| Multi-tenant routing         | SNI/Host-based virtual hosts, one per tenant domain                            |
| Backend isolation            | No tenant cluster is exposed publicly; all traffic flows through Envoy         |
| Admin API exposure           | Bound to `127.0.0.1:9901` only                                                 |
| Config management            | Generated from tenant configs by `tenant_manager.py`                           |
