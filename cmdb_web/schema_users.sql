-- Run once against the cmdb database to add web app support
-- mysql -u root -p cmdb < schema_users.sql

USE cmdb;

CREATE TABLE IF NOT EXISTS users (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(128)  NOT NULL UNIQUE,
    email         VARCHAR(255),
    password_hash VARCHAR(255)  COMMENT 'NULL for LDAP-only users',
    api_key       VARCHAR(128)  UNIQUE COMMENT 'Bearer token for API/CLI access',
    is_admin      TINYINT(1)    NOT NULL DEFAULT 0,
    is_ldap       TINYINT(1)    NOT NULL DEFAULT 0,
    active        TINYINT(1)    NOT NULL DEFAULT 1,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login    DATETIME,
    INDEX idx_api_key (api_key)
) ENGINE=InnoDB;

-- sync_state table (if not already created by cmdb_schema.sql)
CREATE TABLE IF NOT EXISTS sync_state (
    vcenter_url   VARCHAR(255) NOT NULL PRIMARY KEY,
    last_sync_at  DATETIME     NOT NULL,
    last_event_at DATETIME
) ENGINE=InnoDB;
