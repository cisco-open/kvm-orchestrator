-- v0.15.0 — Networks catalog
--
-- Promotes the vestigial `networks` table to a real admin-curated
-- catalog of libvirt bridges/networks. Two flags drive UX:
--
--   user_visible  — non-admin VM-create dropdowns and the GET /api/networks
--                   response filter on this. Defaults to 0 so seeded rows
--                   stay hidden until an admin explicitly opens them.
--   is_default    — exactly one row may carry this; pre-selected for non
--                   admins in Quick Create. Enforced in the service layer
--                   (no DB partial-unique index — MySQL doesn't have those).
--
-- The pre-existing SQLAlchemy model in app/models/network.py is updated
-- alongside this migration; the legacy `subnet` and `available_on_nodes`
-- columns are dropped because nothing read or wrote them.
--
-- Seed strategy: scan `vms.vlan` for distinct bridge names already in use
-- and INSERT IGNORE one row per bridge. user_visible=0, is_default=0 for
-- every seeded row. Re-running the migration after admins have curated
-- rows is a no-op because of the uq_bridge_name unique key + INSERT IGNORE.
-- Bridge name like "ovs-vlan-92" yields vlan_id=92 and name="VLAN 92";
-- a bridge that doesn't end in digits (e.g. "br100" handled; e.g. "virbr0"
-- → vlan_id=0, name="VLAN 0") — admin can rename in the UI.

CREATE TABLE IF NOT EXISTS networks (
  id           INT          NOT NULL AUTO_INCREMENT,
  name         VARCHAR(100) NOT NULL,
  bridge_name  VARCHAR(64)  NOT NULL,
  vlan_id      INT              NULL,
  description  TEXT             NULL,
  user_visible TINYINT(1)   NOT NULL DEFAULT 0,
  is_default   TINYINT(1)   NOT NULL DEFAULT 0,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_bridge_name (bridge_name),
  KEY idx_user_visible (user_visible),
  KEY idx_is_default (is_default)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed from existing distinct vlan strings on vms.
INSERT IGNORE INTO networks (name, bridge_name, vlan_id, description, user_visible, is_default)
SELECT
  CASE
    WHEN t.vlan REGEXP '[0-9]+$'
      THEN CONCAT('VLAN ', REGEXP_SUBSTR(t.vlan, '[0-9]+$'))
    ELSE t.vlan
  END AS name,
  t.vlan AS bridge_name,
  CASE
    WHEN t.vlan REGEXP '[0-9]+$'
      THEN CAST(REGEXP_SUBSTR(t.vlan, '[0-9]+$') AS UNSIGNED)
    ELSE NULL
  END AS vlan_id,
  'Auto-seeded from existing VMs at v0.15.0; set user_visible=1 and (optionally) is_default=1 to expose to non-admins.' AS description,
  0 AS user_visible,
  0 AS is_default
FROM (SELECT DISTINCT vlan FROM vms WHERE vlan IS NOT NULL AND vlan != '') t;
