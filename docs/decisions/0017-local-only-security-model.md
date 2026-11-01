# ADR 0017: Local-only security model without authentication

Status: accepted (2026-11-01). Supersedes: none.

## Context
Lyrveil has one user on one machine. Login would add friction and credential storage with little local protection. However, a service on localhost is still reachable by web pages in the browser: cross-site POSTs, cross-site WebSocket hijacking and DNS rebinding are real threats, and the data is intimate.

## Decision
- No accounts or login in 1.0.
- Publish only `127.0.0.1:4200`.
- Backend checks Host against `localhost:<port>` and `127.0.0.1:<port>`.
- Backend checks Origin on every non-GET request and on WebSocket handshakes.
- JSON content type required. No CORS headers. A strict CSP and `Cross-Origin-Resource-Policy: same-origin`.
- LAN exposure is unsupported and documented as unsafe.

## Alternatives
- Login with a password: useful only with LAN access, which is out of scope. Adds password storage and recovery.
- An API token in browser storage: it does not stop a local attacker, and it complicates the app.
- Doing nothing beyond loopback binding: leaves the browser-based attacks open.

## Consequences
Daily use has no login. Anyone with access to the user's Windows session can open the app, which disk and session locks must cover. Mobile layouts are tested with emulation, not from a real phone over the network. Adding LAN access later needs authentication and a new ADR.
