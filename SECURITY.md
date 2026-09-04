# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/koedastudio/norrsaga-narrator/security/advisories/new),
not in public issues. Expect an initial response within a week. A fix is
released before details are published.

## Supported versions

Only the latest release receives security fixes.

## Threat model

The sidecar sits next to a user-controlled Audiobookshelf server and holds a
long-lived ABS API token. Reports of particular interest:

- **Auth bypass**: any `/v1` route reachable without a valid ABS user token.
- **Server-token misuse**: caller-controlled input (item ids, voices, session
  ids) reaching ABS or Kokoro URLs, or the filesystem, unvalidated.
- **Cache traversal**: reading or writing outside `CACHE_DIR`.
- **Token leakage**: the server token or callers' tokens in logs, errors, or
  responses.

Out of scope: vulnerabilities in Audiobookshelf or Kokoro themselves, and
deployments that expose the sidecar without TLS.
