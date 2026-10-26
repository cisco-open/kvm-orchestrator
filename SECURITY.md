# Security

## Reporting a vulnerability

Please report security issues privately to the maintainers rather than opening a
public issue. Include steps to reproduce and the affected version.

## Secrets & configuration

KVM Orchestrator ships **no real secrets**. Every credential in the Helm chart and
examples is a placeholder (`CHANGE_ME*`) that you must replace for your own
deployment:

- `FLASK_SECRET_KEY`, `JWT_SECRET_KEY` — generate strong random values.
- `DATABASE_URL`, MySQL `rootPassword` — your own DB credentials.
- `ADMIN_PASSWORD` — the bootstrap admin password (leave empty to disable the
  account once LDAP is configured).
- `LDAP_BIND_PASSWORD` — stored encrypted at rest once set via the UI.
- `AGENT_SHARED_KEY`, `HA_CEPH_KEY`, any notification tokens — your own.

Never commit filled-in values. Keep real secrets in a Kubernetes `Secret` (or an
external secrets manager), not in your values file in git. The bootstrap admin
password is read from an externally-managed Secret so it survives upgrades
without ever living in chart state or git.
