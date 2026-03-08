# ZTAM Client Integration Patterns

This document is the fast decision guide for real client onboarding.

Use it when a client shows you their app and you need to answer four questions quickly:

1. What type of client app is this?
2. Which ZTAM login mode should be used?
3. Do we need database identity federation or not?
4. What inputs must we collect before onboarding?

It is intentionally practical. This is the operator-facing decision document, not a marketing summary.

## 1. Quick Decision Table

| Client pattern                                                                          | Typical signs                                                                            | Recommended login mode                                            | DB/SPI needed                                              | Typical effort                               |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------- |
| App already has a login page                                                            | `/login`, password form, redirects to sign-in                                            | `form`                                                            | Optional                                                   | Low to medium                                |
| App has no usable login page                                                            | Direct access to app shell, thin legacy UI, admin panel with no central auth             | `keycloak`                                                        | No, unless they want ZTAM to authenticate against their DB | Low                                          |
| App wants to keep its own auth entirely                                                 | Customer only wants TLS, headers, rate limiting                                          | Usually outside the main ZTAM value path                          | No                                                         | Medium, because the security story is weaker |
| Client has its own user database and wants ZTAM or Keycloak to authenticate those users | They already store usernames and password hashes in MySQL or PostgreSQL                  | `form` or `keycloak`, depending on UX                             | Yes                                                        | Medium                                       |
| SPA plus API                                                                            | Frontend is JavaScript-heavy, API calls expect authenticated browser or session behavior | Usually `keycloak` unless the existing login UX must be preserved | Optional                                                   | Medium                                       |
| Private-network or VPS-hosted internal app                                              | Backend URL is internal IP or private hostname                                           | `form` or `keycloak`                                              | Optional                                                   | Low                                          |

## 2. The Main Rule

Do not start with implementation details.

Start with this decision order:

1. Is the backend reachable from ZTAM?
2. Does the app already own login UX that should be preserved?
3. Does the client want Keycloak to become the identity authority?
4. Does the client want to reuse an existing users database?
5. Can access rules be expressed by hostname, path, method, and role?

If the answer to these is mostly yes, the client is probably a good ZTAM fit.

## 3. Pattern A: Client App Already Has Its Own Login Page

Use this when the app already presents a sign-in form and the client wants the user experience to stay familiar.

### Best fit

- existing `/login` or `/signin` page
- browser-based application
- client wants minimal visible UX change
- app is already used by staff or customers who know the current login flow

### Recommended approach

- onboard with `login_mode: form`
- let ZTAM intercept the login POST
- let Keycloak perform authentication
- optionally use SPI if credentials should be verified against the client's database

### Good questions to ask

- What exact path handles login submission?
- Does the app use redirects after login?
- Does it set its own cookies?
- Does it hardcode absolute URLs?
- Does the backend require a specific token or only trusted identity headers?

## 4. Pattern B: Client App Has No Usable Login Page

Use this when the app should not own login at all.

### Best fit

- old admin portal
- internal dashboard with weak auth
- backend app that should just trust platform identity
- customer is open to a platform-owned sign-in experience

### Recommended approach

- onboard with `login_mode: keycloak`
- redirect unauthenticated users to the ZTAM or Keycloak login flow
- keep the application focused on business functionality, not identity

## 5. Pattern C: Client Wants To Keep Its Own Auth

This is not the strongest ZTAM story, but it is a possible client request.

### What to say

This is possible, but it is not full ZTAM protection.

If ZTAM is not the identity and policy decision point, then the project becomes more of a secure reverse proxy than a centralized Zero Trust access platform.

### Recommendation

- only accept this if the client explicitly wants reduced scope
- document that centralized auth enforcement is not active for that tenant
- treat it as an exception, not the default model

## 6. Pattern D: Client Has Its Own User Database

This is the most important identity variation to understand.

### There are two valid strategies

#### Option 1: Do not read the client DB

Use this when:

- the client wants Keycloak-managed users
- the client can provision users in Keycloak or federate some other way
- the client does not want ZTAM touching their application database

#### Option 2: Federate Keycloak against the client DB

Use this when:

- the client already has users in MySQL or PostgreSQL
- the client wants those users to keep logging in with existing credentials
- the client can provide a read-only DB account
- password hashes are in a supported format

### In this repo, DB federation means

- Keycloak queries the client users table through the SPI
- Keycloak fetches the stored password hash
- Keycloak compares the submitted password to that stored hash
- ZTAM still uses normal Keycloak session and token flow after verification

### Minimum DB inputs to collect

- database engine: MySQL or PostgreSQL
- host
- port
- database name
- read-only username
- read-only password
- users table name
- username or email column
- password hash column
- role column if available
- hash algorithm, ideally bcrypt

### What not to request

- root database access
- write permissions
- plaintext passwords

## 7. Pattern E: SPA Plus API

Use caution here.

### Typical signs

- JavaScript frontend talks heavily to API endpoints
- app stores tokens in browser storage
- routing and redirect logic are mostly client-side

### Recommendation

- prefer `keycloak` mode unless preserving existing login UX is a hard requirement
- validate browser redirects and session assumptions early
- confirm whether the frontend expects a local JWT format or can simply rely on session-backed requests

## 8. Client Discovery Checklist

Use this list in the first meeting.

### Business and ownership

- Customer name
- Technical owner
- DNS owner
- Environment owner
- Who approves role rules

### Application shape

- Public URL or backend URL
- Is it browser-first, API-first, or mixed?
- Does it already have a login page?
- Are any routes public by design?
- Which routes are sensitive?

### Identity

- Does the client want ZTAM or Keycloak to own login?
- Do they want to keep existing users?
- Are users already stored in a database?
- If yes, which database engine and hash algorithm?

### Authorization

- What are the real business roles?
- Which paths must be admin-only?
- Which methods should be blocked for limited roles?
- Do they need tenant-specific exceptions?

### Technical risks

- Hardcoded absolute URLs
- Existing cookies
- Reverse-proxy incompatibilities
- CORS assumptions
- App-generated redirects

## 9. Default Recommendation For A First Client

If the first client arrives and you have limited time, use this default decision model:

1. Run `python3 scripts/tenant_manager.py assess --backend-url <client-url> --name <tenant> --hostname <host> --roles "admin,manager,user" --write-config`.
2. If the site clearly has its own login page, start with `form` mode.
3. If the site does not have a clean login flow, use `keycloak` mode.
4. Only use DB federation if the client explicitly wants to reuse existing user credentials and can provide read-only DB access plus hash details.
5. Never promise zero work before checking redirects, cookies, and route behavior.

## 10. Red Flags That Require Caution

- The app redirects to many absolute URLs on another host.
- The app requires browser-local JWT storage to function.
- The client does not know who owns DNS.
- The client cannot provide test users.
- The client wants DB-backed login but cannot describe the password hash format.
- The app relies on fragile session cookies tied to the old hostname.
- The customer wants to keep all current auth logic and still claims they want full Zero Trust centralization.

These do not always block onboarding, but they move the tenant into `needs-review` territory.

## 11. Required Outputs After Discovery

After the first call, you should be able to produce:

1. A tenant name and hostname.
2. A backend URL.
3. A recommended login mode.
4. A role list.
5. A note on whether DB federation is required.
6. A short list of client-side adaptation risks.

If you cannot produce those six items, the integration is not ready for onboarding yet.
