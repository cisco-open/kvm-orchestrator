// Default runtime config. The UI container entrypoint overwrites this file at
// startup from the ENVIRONMENT and BRANDING pod env vars. Kept in public/ so
// `vite dev` and a plain build serve a sane default: the debranded open-source
// KVMaestro build, live (blue) environment.
window.__ENV__ = { ENVIRONMENT: "live", BRANDING: "kvmaestro" };
