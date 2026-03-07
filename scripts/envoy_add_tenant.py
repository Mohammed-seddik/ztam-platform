#!/usr/bin/env python3
"""
Insert a new tenant virtual host and cluster into Envoy's envoy.yaml.
Uses sentinel comments as safe insertion points.
Called by onboard-tenant.sh — do not run directly unless you know what you're doing.
"""
import argparse
import sys

ROUTES_MARKER  = "# __ZTAM_TENANT_ROUTES__ (do not remove — used by onboard-tenant.sh)"
CLUSTERS_MARKER = "# __ZTAM_TENANT_CLUSTERS__ (do not remove — used by onboard-tenant.sh)"

SECURITY_HEADERS = """\
                      response_headers_to_add:
                        - header:
                            key: Strict-Transport-Security
                            value: "max-age=63072000; includeSubDomains; preload"
                        - header:
                            key: X-Content-Type-Options
                            value: "nosniff"
                        - header:
                            key: X-Frame-Options
                            value: "SAMEORIGIN"
                        - header:
                            key: Referrer-Policy
                            value: "strict-origin-when-cross-origin"
                        - header:
                            key: Content-Security-Policy
                            value: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
                        - header:
                            key: Permissions-Policy
                            value: "geolocation=(), microphone=(), camera=(), payment=()"
                        - header:
                            key: Cache-Control
                            value: "no-store"\
"""


def vhost_snippet(name: str, hostname: str, login_mode: str = "form") -> str:
    routes = ""
    
    if login_mode == "keycloak":
        # Redirect unauthenticated requests to the ZTAM login-redirect endpoint
        # which will then send them to Keycloak.
        routes += f"""\
                        - match:
                            prefix: "/"
                          route:
                            cluster: auth_middleware_cluster
                            prefix_rewrite: "/login-redirect?tenant={name}&redirect_uri="
                            timeout: 5s
                          typed_per_filter_config:
                            envoy.filters.http.ext_authz:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
                              disabled: true
"""
    
    routes += f"""\
                        - match:
                            path: "/api/auth/login"
                          route:
                            cluster: auth_middleware_cluster
                            prefix_rewrite: "/login-proxy"
                            timeout: 10s
                          typed_per_filter_config:
                            envoy.filters.http.ext_authz:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
                              disabled: true
                        - match:
                            path: "/api/auth/logout"
                          route:
                            cluster: auth_middleware_cluster
                            prefix_rewrite: "/logout"
                            timeout: 10s
                          typed_per_filter_config:
                            envoy.filters.http.ext_authz:
                              "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
                              disabled: true
                        - match:
                            prefix: "/"
                          route:
                            cluster: {name}_cluster
                            timeout: 30s

"""

    return f"""\
                    # ── Tenant: {name} ({login_mode} mode) ────────────────────
                    - name: {name}_vhost
                      domains: ["{hostname}"]
{SECURITY_HEADERS}
                      routes:
{routes}
"""


def cluster_snippet(name: str, host: str, port: str, tls: str) -> str:
    tls_block = ""
    if tls == "true":
        tls_block = f"""\
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: {host}
"""
    return f"""\
    # ── Tenant: {name} ─────────────────────────────────────────────────────
    - name: {name}_cluster
      connect_timeout: 10s
      type: LOGICAL_DNS
      dns_lookup_family: V4_ONLY
{tls_block}      load_assignment:
        cluster_name: {name}_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {host}
                      port_value: {port}

"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name",         required=True)
    p.add_argument("--hostname",     required=True)
    p.add_argument("--backend-host", required=True)
    p.add_argument("--backend-port", required=True)
    p.add_argument("--backend-tls",  required=True, choices=["true", "false"])
    p.add_argument("--login-mode",   default="form", choices=["form", "keycloak"])
    p.add_argument("--envoy-yaml",   required=True)
    args = p.parse_args()

    with open(args.envoy_yaml, "r", encoding="utf-8") as f:
        content = f.read()

    # Idempotency — skip if tenant already exists
    if f"{args.name}_cluster" in content:
        print(f"   ⚠  Tenant '{args.name}' already present in envoy.yaml — skipping")
        return

    # Insert virtual host before the default wildcard vhost
    if ROUTES_MARKER not in content:
        print("ERROR: Route sentinel marker not found in envoy.yaml.")
        print("       Expected: # __ZTAM_TENANT_ROUTES__")
        sys.exit(1)

    # Insert cluster before the end-of-clusters sentinel
    if CLUSTERS_MARKER not in content:
        print("ERROR: Cluster sentinel marker not found in envoy.yaml.")
        print("       Expected: # __ZTAM_TENANT_CLUSTERS__")
        sys.exit(1)

    content = content.replace(
        ROUTES_MARKER,
        vhost_snippet(args.name, args.hostname, args.login_mode) + "                    " + ROUTES_MARKER
    )
    content = content.replace(
        CLUSTERS_MARKER,
        cluster_snippet(args.name, args.backend_host, args.backend_port, args.backend_tls)
        + "    " + CLUSTERS_MARKER
    )

    with open(args.envoy_yaml, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"   ✓ envoy.yaml updated — added '{args.name}' vhost + cluster")


if __name__ == "__main__":
    main()
