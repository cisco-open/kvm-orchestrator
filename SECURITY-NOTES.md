# Accepted / deferred dependency advisories

This file tracks known advisories that are intentionally NOT remediated by a
version bump, with the rationale. Everything else is kept current (see the
`requirements.txt` files and `frontend/package.json`).

## paramiko — PYSEC-2026-2858 (SHA-1 allowed in rsakey.py)

- **Status:** accepted (no upstream fix in any released version, incl. 4.0.0).
- **What it is:** paramiko permits the legacy SHA-1 RSA signature algorithm.
  It is a crypto-hygiene hardening item, not a remote-code-execution bug.
- **Risk here:** low. paramiko is used for controller→KVM-node SSH. Modern
  OpenSSH servers do not negotiate SHA-1 (`ssh-rsa`) by default, so in practice
  the stronger `rsa-sha2-256/512` algorithms are used.
- **Action:** stay on the latest paramiko 3.5.x; re-evaluate when upstream ships
  a fix (track the advisory).

## react-router-dom — GHSA-wrjc-x8rr-h8h6 / GHSA-337j-9hxr-rhxg

- **Status:** deferred (fix requires the react-router v6 → v7 breaking major).
- **What it is:** (1) an open redirect via a backslash in `<Link>`/`useNavigate`
  and (2) arbitrary constructor injection via `deserializeErrors()` in **SSR**
  hydration.
- **Risk here:** low. This UI is a **client-side SPA** — it does not use
  react-router SSR, so the hydration advisory does not apply. The open-redirect
  path is low impact for an authenticated internal console.
- **Action:** fold the v6 → v7 upgrade into a planned, separately-tested change
  (routing regression pass) rather than a security patch bump.
