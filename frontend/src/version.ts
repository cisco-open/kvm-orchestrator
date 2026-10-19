export const VERSION = "v0.34.0";
export const RELEASE_NOTE = "HA reliability release: Readmit now works for 'failed' nodes (not just 'fenced'); a mass-failure circuit breaker stops a controller-side blip from flagging the whole fleet; nodes flagged 'failed' auto-recover once they heartbeat again; and OVS VLAN bridges on the nodes are auto-discovered into the networks catalog.";
