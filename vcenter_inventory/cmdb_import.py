#!/usr/bin/env python3
"""
cmdb_import.py  –  Import vcenter_inventory.csv into the CMDB MySQL database.

Features:
  - Idempotent: re-running updates existing rows, inserts new ones
  - Marks nodes NOT seen in the current CSV as inactive (active=0)
  - Normalises OS, environment, tier, owner into lookup tables
  - Stores tags (many-to-many) and primary IP per node
  - Records each run in scan_runs

Usage:
  python3 cmdb_import.py --csv vcenter_inventory.csv \\
      [--host 127.0.0.1] [--port 3306] [--user root] [--password Pay4mysql!] \\
      [--db cmdb]
"""

import csv, argparse, sys, os, re
from datetime import datetime
import pymysql
import pymysql.cursors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def categorize_os(full_name):
    if not full_name:
        return "other"
    n = full_name.lower()
    if "windows" in n:
        return "windows"
    if any(x in n for x in ["linux","centos","redhat","oracle","ubuntu","debian",
                              "suse","amazon","fedora","rocky","alma","photon"]):
        return "linux"
    return "other"

def normalize_os_name(full_name):
    """Shorten verbose VMware Tools OS strings to a human-readable form."""
    if not full_name:
        return full_name
    # Flatcar: "Linux 6.12.74-flatcar Flatcar Container Linux by Kinvolk 4459.2.4 ..."
    m = re.search(r'Flatcar Container Linux\s+(?:by\s+\S+\s+)?(\d+\.\d+\.\d+)', full_name, re.IGNORECASE)
    if m:
        return f'Flatcar Container Linux {m.group(1)}'
    return full_name

def os_family(full_name):
    if not full_name:
        return None
    n = full_name.lower()
    for f in ["oracle","centos","redhat","ubuntu","debian","suse","amazon",
              "fedora","rocky","alma","photon","windows server","windows 10",
              "windows 11","other linux"]:
        if f in n:
            return f
    return None

def get_or_create(cur, table, col, val, extra=None):
    """Get ID of a row by unique col=val, inserting if missing. Returns id."""
    if not val:
        return None
    cur.execute(f"SELECT id FROM `{table}` WHERE `{col}` = %s", (val,))
    row = cur.fetchone()
    if row:
        return row["id"]
    if extra:
        cols = ", ".join([f"`{col}`"] + [f"`{k}`" for k in extra])
        vals = tuple([val] + list(extra.values()))
        placeholders = ", ".join(["%s"] * len(vals))
        cur.execute(f"INSERT INTO `{table}` ({cols}) VALUES ({placeholders})", vals)
    else:
        cur.execute(f"INSERT INTO `{table}` (`{col}`) VALUES (%s)", (val,))
    return cur.lastrowid

def vcenter_label(url):
    """Derive a short label from a vCenter URL, e.g. 'ev3' from 'ev3vccomp01'."""
    if not url:
        return url
    import re
    m = re.search(r"(ev\d+|na\d+|phx\d+|st\d+)", url.lower())
    return m.group(1) if m else url

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import vCenter CSV into CMDB")
    parser.add_argument("--csv",      default="vcenter_inventory.csv")
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=3306)
    parser.add_argument("--user",     default="root")
    parser.add_argument("--password", default="Pay4mysql!")
    parser.add_argument("--db",       default="cmdb")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"[!] CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    conn = pymysql.connect(
        host=args.host, port=args.port,
        user=args.user, password=args.password,
        db=args.db, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )
    cur = conn.cursor()

    # --- scan_runs entry ---
    cur.execute("INSERT INTO scan_runs (source) VALUES (%s)", (args.csv,))
    scan_id = cur.lastrowid
    conn.commit()
    print(f"[*] Scan run ID: {scan_id}", flush=True)

    # --- read CSV ---
    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"[*] Loaded {len(rows)} rows from {args.csv}", flush=True)

    imported = 0
    seen_node_ids = []

    for i, row in enumerate(rows):
        name = (row.get("name") or "").strip()
        if not name:
            continue

        # --- Lookup / create normalised FK rows ---
        # vCenter
        vc_url   = (row.get("vcenter_url") or "").strip()
        vc_id    = get_or_create(cur, "vcenters", "url", vc_url,
                                 {"label": vcenter_label(vc_url)})

        # Datacenter
        dc_path  = (row.get("datacenter") or "").strip()
        dc_name  = dc_path.lstrip("/")
        cur.execute("SELECT id FROM datacenters WHERE path=%s AND vcenter_id=%s",
                    (dc_path, vc_id))
        dc_row = cur.fetchone()
        if dc_row:
            dc_id = dc_row["id"]
        else:
            cur.execute("INSERT INTO datacenters (vcenter_id, name, path) VALUES (%s,%s,%s)",
                        (vc_id, dc_name, dc_path))
            dc_id = cur.lastrowid

        # OS
        os_full = normalize_os_name((row.get("guest_os") or "").strip())
        os_id   = None
        if os_full:
            cur.execute("SELECT id FROM operating_systems WHERE full_name=%s", (os_full,))
            os_row = cur.fetchone()
            if os_row:
                os_id = os_row["id"]
            else:
                cur.execute(
                    "INSERT INTO operating_systems (full_name, category, family) VALUES (%s,%s,%s)",
                    (os_full, categorize_os(os_full), os_family(os_full))
                )
                os_id = cur.lastrowid

        env_id   = get_or_create(cur, "environments", "name", row.get("VI.ENV") or None)
        tier_id  = get_or_create(cur, "tiers",        "name", row.get("VI.TIER") or None)
        owner_id = get_or_create(cur, "owners",       "name", row.get("VI.OWNER") or None)

        power = (row.get("power_state") or "unknown").strip()
        if power not in ("poweredOn","poweredOff","suspended"):
            power = "unknown"

        try:
            cpus = int(row.get("cpus") or 0) or None
        except ValueError:
            cpus = None
        try:
            mem = float(row.get("memory_gb") or 0) or None
        except ValueError:
            mem = None

        # --- Upsert node ---
        moref = (row.get("moref") or "").strip() or None

        # Primary lookup by stable MoRef; fallback to name+datacenter for rows without moref
        existing = None
        if moref and dc_id:
            cur.execute(
                "SELECT id, name, hostname, power_state, cpus, memory_gb, os_id, "
                "environment_id, tier_id, owner_id, purpose, landscape, app_name, "
                "description, deployment, cmdb_uuid, vcenter_path, active, is_template, status_id "
                "FROM nodes WHERE moref=%s AND datacenter_id=%s",
                (moref, dc_id))
            existing = cur.fetchone()
        if not existing:
            cur.execute(
                "SELECT id, name, hostname, power_state, cpus, memory_gb, os_id, "
                "environment_id, tier_id, owner_id, purpose, landscape, app_name, "
                "description, deployment, cmdb_uuid, vcenter_path, active, is_template, status_id "
                "FROM nodes WHERE name=%s AND datacenter_id=%s AND (moref IS NULL OR moref='')",
                (name, dc_id))
            existing = cur.fetchone()

        now = datetime.now()
        fields = dict(
            name           = name,
            moref          = moref,
            hostname       = (row.get("hostname") or "").strip() or None,
            vcenter_path   = (row.get("vcenter_path") or "").strip() or None,
            datacenter_id  = dc_id,
            os_id          = os_id,
            environment_id = env_id,
            tier_id        = tier_id,
            owner_id       = owner_id,
            power_state    = power,
            cpus           = cpus,
            memory_gb      = mem,
            purpose        = (row.get("VI.PURPOSE") or "").strip() or None,
            landscape      = (row.get("VI.LANDSCAPE") or "").strip() or None,
            app_name       = (row.get("App_Name") or "").strip() or None,
            description    = (row.get("Description") or "").strip() or None,
            deployment     = (row.get("deployment") or "").strip() or None,
            cmdb_uuid      = (row.get("cmdb_uuid") or "").strip() or None,
            last_seen      = now,
            last_scan_id   = scan_id,
            active         = 1,
            is_template    = 1 if str(row.get("is_template") or "").lower() in ("1","true","yes") else 0,
        )

        # Auto-set screamtest status; otherwise preserve existing, or setup (6) for new VMs
        if 'screamtest' in name.lower():
            fields['status_id'] = 5
        elif existing:
            fields['status_id'] = existing.get('status_id')
        else:
            fields['status_id'] = 6  # setup

        if existing:
            node_id = existing["id"]
            # Log changes for tracked fields
            tracked = ['name', 'hostname', 'power_state', 'cpus', 'memory_gb', 'os_id',
                       'environment_id', 'tier_id', 'owner_id', 'purpose',
                       'landscape', 'app_name', 'description', 'deployment',
                       'cmdb_uuid', 'vcenter_path', 'is_template', 'status_id']
            if existing.get('active') == 0:
                tracked.append('active')
            for f in tracked:
                old = existing.get(f)
                new = fields.get(f)
                if str(old or '') != str(new or ''):
                    cur.execute(
                        "INSERT INTO node_history (node_id, field, old_value, new_value, source) "
                        "VALUES (%s,%s,%s,%s,'sync')",
                        (node_id, f, str(old) if old is not None else None,
                         str(new) if new is not None else None))
            set_clause = ", ".join([f"`{k}`=%s" for k in fields])
            cur.execute(f"UPDATE nodes SET {set_clause} WHERE id=%s",
                        list(fields.values()) + [node_id])
        else:
            # Replacement detection: same name+datacenter but different moref
            replaced_node = None
            if moref and dc_id:
                cur.execute(
                    "SELECT id FROM nodes WHERE name=%s AND datacenter_id=%s "
                    "AND moref IS NOT NULL AND moref != %s AND active=1",
                    (name, dc_id, moref))
                replaced_node = cur.fetchone()

            fields["first_seen"] = now
            cols = ", ".join([f"`{k}`" for k in fields])
            placeholders = ", ".join(["%s"] * len(fields))
            cur.execute(f"INSERT INTO nodes ({cols}) VALUES ({placeholders})",
                        list(fields.values()))
            node_id = cur.lastrowid
            cur.execute(
                "INSERT INTO node_history (node_id, field, old_value, new_value, source) "
                "VALUES (%s,'_created',NULL,'1','sync')",
                (node_id,))

            if replaced_node:
                old_id = replaced_node['id']
                cur.execute("UPDATE nodes SET active=0, status_id=4 WHERE id=%s", (old_id,))
                cur.execute("SELECT group_id FROM group_members WHERE node_id=%s", (old_id,))
                for grp_row in cur.fetchall():
                    cur.execute(
                        "INSERT IGNORE INTO group_members (node_id, group_id) VALUES (%s,%s)",
                        (node_id, grp_row['group_id']))
                cur.execute(
                    "INSERT INTO node_history (node_id, field, old_value, new_value, source) "
                    "VALUES (%s,'_replaced',%s,%s,'sync')",
                    (node_id, str(old_id), str(node_id)))
                print(f"    [replace] {name}: node {old_id} retired → new node {node_id}, groups copied", flush=True)

        seen_node_ids.append(node_id)

        # --- IP address ---
        ip = (row.get("ip_address") or "").strip()
        if ip:
            cur.execute(
                "INSERT INTO ip_addresses (node_id, ip, is_primary) VALUES (%s,%s,1) "
                "ON DUPLICATE KEY UPDATE is_primary=1",
                (node_id, ip)
            )

        # --- Tags ---
        tags_raw = (row.get("tags") or "").strip()
        if tags_raw:
            cur.execute("DELETE FROM node_tags WHERE node_id=%s", (node_id,))
            for tag_name in tags_raw.split("|"):
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                tag_id = get_or_create(cur, "tags", "name", tag_name)
                cur.execute(
                    "INSERT IGNORE INTO node_tags (node_id, tag_id) VALUES (%s,%s)",
                    (node_id, tag_id)
                )

        imported += 1
        if imported % 500 == 0:
            conn.commit()
            print(f"    {imported}/{len(rows)} imported...", flush=True)

    conn.commit()

    # --- Mark nodes not in this scan as inactive ---
    if seen_node_ids:
        placeholders = ",".join(["%s"] * len(seen_node_ids))
        cur.execute(
            f"UPDATE nodes SET active=0 WHERE id NOT IN ({placeholders}) AND active=1",
            seen_node_ids
        )
        deactivated = cur.rowcount
        conn.commit()
    else:
        deactivated = 0

    # --- Close scan_run ---
    cur.execute("UPDATE scan_runs SET finished_at=%s WHERE id=%s",
                (datetime.now(), scan_id))
    conn.commit()
    conn.close()

    print(f"\n[+] Import complete.")
    print(f"    Imported/updated : {imported}")
    print(f"    Marked inactive  : {deactivated}")
    print(f"    Scan run ID      : {scan_id}")


if __name__ == "__main__":
    main()
