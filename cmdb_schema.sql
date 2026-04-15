-- =============================================================================
-- CMDB Schema  –  vCenter VM inventory
-- MySQL 5.7
-- =============================================================================

CREATE DATABASE IF NOT EXISTS cmdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE cmdb;

-- ---------------------------------------------------------------------------
-- scan_runs  –  each time vcenter_inventory.py is run, one row is inserted.
--               Lets you see staleness and diff between runs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scan_runs (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    started_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    source      VARCHAR(255) COMMENT 'e.g. vcenter_inventory.py',
    notes       VARCHAR(512),
    INDEX idx_started (started_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- vcenters  –  one row per vCenter URL
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vcenters (
    id          SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    url         VARCHAR(255) NOT NULL UNIQUE,
    label       VARCHAR(64)  COMMENT 'friendly name, e.g. na1, ev3',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- datacenters  –  one row per vCenter datacenter path
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datacenters (
    id          SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    vcenter_id  SMALLINT UNSIGNED NOT NULL,
    name        VARCHAR(128) NOT NULL COMMENT 'e.g. PHX1-THRYV-DC, ev3dccomp01',
    path        VARCHAR(255) NOT NULL COMMENT 'full govc path, e.g. /PHX1-THRYV-DC',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_dc_path (vcenter_id, path),
    FOREIGN KEY (vcenter_id) REFERENCES vcenters(id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- operating_systems  –  normalised OS names
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operating_systems (
    id          SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    full_name   VARCHAR(255) NOT NULL UNIQUE COMMENT 'raw guestFullName from vCenter',
    category    ENUM('linux','windows','other') NOT NULL DEFAULT 'other',
    family      VARCHAR(64)  COMMENT 'e.g. oracle, centos, windows-server',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- environments  –  normalised from VI.ENV  (PROD, DR, DEV, …)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environments (
    id    TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(64) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- tiers  –  normalised from VI.TIER  (Production, Non-Production, …)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tiers (
    id    TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(64) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- owners  –  normalised from VI.OWNER / Owner columns
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS owners (
    id         SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(255) NOT NULL UNIQUE,
    email      VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- nodes  –  one row per VM.  Core CMDB table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(255) NOT NULL          COMMENT 'VM name in vCenter',
    hostname        VARCHAR(255)                   COMMENT 'guest hostname reported by VMware tools',
    vcenter_path    VARCHAR(512)                   COMMENT 'full inventory path in vCenter',
    datacenter_id   SMALLINT UNSIGNED              COMMENT 'FK → datacenters',
    os_id           SMALLINT UNSIGNED              COMMENT 'FK → operating_systems',
    environment_id  TINYINT UNSIGNED               COMMENT 'FK → environments (VI.ENV)',
    tier_id         TINYINT UNSIGNED               COMMENT 'FK → tiers (VI.TIER)',
    owner_id        SMALLINT UNSIGNED              COMMENT 'FK → owners (VI.OWNER)',
    power_state     ENUM('poweredOn','poweredOff','suspended','unknown')
                    NOT NULL DEFAULT 'unknown',
    cpus            TINYINT UNSIGNED,
    memory_gb       DECIMAL(8,1),
    purpose         VARCHAR(512)                   COMMENT 'VI.PURPOSE free-text',
    landscape       VARCHAR(128)                   COMMENT 'VI.LANDSCAPE',
    app_name        VARCHAR(255)                   COMMENT 'App_Name custom attr',
    description     TEXT                           COMMENT 'Description custom attr',
    deployment      VARCHAR(128)                   COMMENT 'deployment custom attr',
    cmdb_uuid       VARCHAR(128)                   COMMENT 'cmdb_uuid from vCenter',
    first_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
    last_scan_id    INT UNSIGNED                   COMMENT 'FK → scan_runs',
    active          TINYINT(1) NOT NULL DEFAULT 1  COMMENT '0 = not seen in last scan',
    INDEX idx_name        (name),
    INDEX idx_hostname    (hostname),
    INDEX idx_power       (power_state),
    INDEX idx_os          (os_id),
    INDEX idx_env         (environment_id),
    INDEX idx_tier        (tier_id),
    INDEX idx_owner       (owner_id),
    INDEX idx_dc          (datacenter_id),
    INDEX idx_last_seen   (last_seen),
    INDEX idx_active      (active),
    FOREIGN KEY (datacenter_id)  REFERENCES datacenters(id),
    FOREIGN KEY (os_id)          REFERENCES operating_systems(id),
    FOREIGN KEY (environment_id) REFERENCES environments(id),
    FOREIGN KEY (tier_id)        REFERENCES tiers(id),
    FOREIGN KEY (owner_id)       REFERENCES owners(id),
    FOREIGN KEY (last_scan_id)   REFERENCES scan_runs(id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- ip_addresses  –  one-to-many: a node can have multiple IPs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ip_addresses (
    id         INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    node_id    INT UNSIGNED NOT NULL,
    ip         VARCHAR(45) NOT NULL   COMMENT 'IPv4 or IPv6',
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    source     VARCHAR(32) DEFAULT 'vmware-tools' COMMENT 'vmware-tools, manual, etc.',
    INDEX idx_ip      (ip),
    INDEX idx_node    (node_id),
    UNIQUE KEY uq_node_ip (node_id, ip),
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- tags  –  tag catalogue (name + optional category from vCenter)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id          SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(255) NOT NULL UNIQUE,
    category    VARCHAR(128)          COMMENT 'vCenter tag category',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- node_tags  –  many-to-many: nodes ↔ tags
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS node_tags (
    node_id  INT UNSIGNED     NOT NULL,
    tag_id   SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (node_id, tag_id),
    INDEX idx_tag  (tag_id),
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)  REFERENCES tags(id)  ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- node_attributes  –  flexible EAV for any extra k/v from vCenter or manual
--                     (use sparingly — prefer columns on nodes for core data)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS node_attributes (
    id       INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    node_id  INT UNSIGNED NOT NULL,
    name     VARCHAR(128) NOT NULL,
    value    TEXT,
    INDEX idx_node (node_id),
    INDEX idx_name (name),
    UNIQUE KEY uq_node_attr (node_id, name),
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- node_groups  –  logical groupings for Ansible / Nagios
--                 (ansible_group, nagios_hostgroup, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS groups_ (
    id          SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(128) NOT NULL UNIQUE,
    type        ENUM('ansible','nagios','custom') NOT NULL DEFAULT 'custom',
    description VARCHAR(255),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS node_groups (
    node_id   INT UNSIGNED      NOT NULL,
    group_id  SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (node_id, group_id),
    INDEX idx_group (group_id),
    FOREIGN KEY (node_id)  REFERENCES nodes(id)   ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES groups_(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- Useful views
-- ---------------------------------------------------------------------------

-- Full node view — everything denormalized for easy querying
CREATE OR REPLACE VIEW v_nodes AS
SELECT
    n.id,
    n.name,
    n.hostname,
    n.power_state,
    n.cpus,
    n.memory_gb,
    o.full_name   AS guest_os,
    o.category    AS os_category,
    e.name        AS environment,
    t.name        AS tier,
    ow.name       AS owner,
    n.purpose,
    n.landscape,
    n.app_name,
    n.description,
    n.deployment,
    n.cmdb_uuid,
    ip.ip         AS primary_ip,
    dc.path       AS datacenter,
    vc.url        AS vcenter_url,
    vc.label      AS vcenter_label,
    n.vcenter_path,
    n.first_seen,
    n.last_seen,
    n.active
FROM nodes n
LEFT JOIN operating_systems o  ON o.id  = n.os_id
LEFT JOIN environments      e  ON e.id  = n.environment_id
LEFT JOIN tiers             t  ON t.id  = n.tier_id
LEFT JOIN owners            ow ON ow.id = n.owner_id
LEFT JOIN datacenters       dc ON dc.id = n.datacenter_id
LEFT JOIN vcenters          vc ON vc.id = dc.vcenter_id
LEFT JOIN ip_addresses      ip ON ip.node_id = n.id AND ip.is_primary = 1;

-- Node + all tags concatenated
CREATE OR REPLACE VIEW v_node_tags AS
SELECT
    n.id,
    n.name,
    GROUP_CONCAT(tg.name ORDER BY tg.name SEPARATOR '|') AS tags
FROM nodes n
LEFT JOIN node_tags nt ON nt.node_id = n.id
LEFT JOIN tags      tg ON tg.id      = nt.tag_id
GROUP BY n.id, n.name;

-- Ansible-ready: name, ip, os_category, environment, tier, tags
CREATE OR REPLACE VIEW v_ansible AS
SELECT
    n.name,
    ip.ip         AS ansible_host,
    o.category    AS os_category,
    e.name        AS environment,
    t.name        AS tier,
    ow.name       AS owner,
    n.app_name,
    dc.path       AS datacenter,
    GROUP_CONCAT(DISTINCT tg.name ORDER BY tg.name SEPARATOR '|') AS tags
FROM nodes n
LEFT JOIN operating_systems o  ON o.id  = n.os_id
LEFT JOIN environments      e  ON e.id  = n.environment_id
LEFT JOIN tiers             t  ON t.id  = n.tier_id
LEFT JOIN owners            ow ON ow.id = n.owner_id
LEFT JOIN datacenters       dc ON dc.id = n.datacenter_id
LEFT JOIN ip_addresses      ip ON ip.node_id = n.id AND ip.is_primary = 1
LEFT JOIN node_tags         nt ON nt.node_id = n.id
LEFT JOIN tags              tg ON tg.id      = nt.tag_id
WHERE n.active = 1 AND n.power_state = 'poweredOn'
GROUP BY n.id;
