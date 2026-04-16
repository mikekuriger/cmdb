#!/usr/bin/env python3
"""
cmdb_sync_fast.py  –  Fast vCenter → CMDB sync via direct govc scan.

Run every 30 minutes via cron. Queries all VMs directly from vCenter using
parallel workers (like the full inventory script), then upserts changes into
the CMDB and marks VMs no longer in vCenter as inactive.

Uses threading to parallelize govc vm.info calls — similar to vcenter_inventory.py.
Does NOT depend on the vCenter events API (CreateCollectorForEvents), which can
fail with "operation not allowed in current state" when too many collectors exist.

Usage:
  python3 cmdb_sync_fast.py \\
      [--env ~/vcenter_inventory/na1 ~/vcenter_inventory/ev3] \\
      [--host 127.0.0.1] [--port 3306] [--user root] [--password Pay4mysql!] \\
      [--db cmdb] [--workers 20] [--dry-run]
"""

import subprocess, json, re, argparse, os, sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymysql
import pymysql.cursors

BATCH_SIZE = 50   # VM paths per govc vm.info -json call


def load_env_file(path):
    env = os.environ.copy()
    path = os.path.expanduser(path)
    with open(path) as f:
        for line in f:
            line = line.strip()
            m = re.match(r"^export\s+(\w+)=['\"]?([^'\"#\n]*)['\"]?", line)
            if m:
                env[m.group(1)] = m.group(2)
    return env


def govc_run(env, *args):
    result = subprocess.run(["govc"] + list(args),
                            capture_output=True, text=True, env=env)
    return result.stdout.strip(), result.stderr.strip()


def govc_json(env, *args):
    stdout, _ = govc_run(env, *args)
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def get_datacenter(env):
    stdout, _ = govc_run(env, "ls", "/")
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    return lines[0] if lines else None


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


def build_field_map(env):
    stdout, _ = govc_run(env, "fields.ls")
    field_map = {}
    target = {"VI.ENV","VI.LANDSCAPE","VI.OWNER","VI.PURPOSE","VI.TIER",
              "App_Name","Description","Owner","deployment","cmdb_uuid"}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                key_id = int(parts[0])
            except ValueError:
                continue
            if parts[1] in target:
                field_map[key_id] = parts[1]
    return field_map


def fetch_vm_batch(env, batch):
    """Fetch vm.info -json for a batch of VM paths. Returns list of (path, vm_dict)."""
    data = govc_json(env, "vm.info", "-json", *batch)
    if not data or not data.get("virtualMachines"):
        return []

    results = []
    for v in data["virtualMachines"]:
        cfg_name = (v.get("config") or {}).get("name", "")
        # Match back to the original path by VM name
        matching = [p for p in batch
                    if p.rstrip("/").split("/")[-1] == cfg_name]
        path = matching[0] if matching else f"(unknown)/{cfg_name}"
        results.append((path, v))
    return results


def get_or_create(cur, table, col, val, extra=None):
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


def upsert_vm(cur, v, vm_path, field_map, vcenter_url, datacenter, scan_id):
    """Upsert a single VM from pre-fetched vm.info data. Returns node_id or None."""
    cfg   = v.get("config") or {}
    rt    = v.get("runtime") or {}
    guest = v.get("guest") or {}
    hw    = cfg.get("hardware") or {}

    name  = cfg.get("name", "").strip()
    moref = (v.get("self") or {}).get("value", "").strip()
    if not name:
        return None

    cur.execute("SELECT id FROM vcenters WHERE url=%s", (vcenter_url,))
    vc_row = cur.fetchone()
    vc_id  = vc_row["id"] if vc_row else get_or_create(cur, "vcenters", "url", vcenter_url)

    dc_path = datacenter
    dc_name = dc_path.lstrip("/")
    cur.execute("SELECT id FROM datacenters WHERE path=%s AND vcenter_id=%s", (dc_path, vc_id))
    dc_row = cur.fetchone()
    dc_id  = dc_row["id"] if dc_row else None

    os_full = cfg.get("guestFullName", "").strip()
    os_id   = None
    if os_full:
        cur.execute("SELECT id FROM operating_systems WHERE full_name=%s", (os_full,))
        os_row = cur.fetchone()
        if os_row:
            os_id = os_row["id"]
        else:
            cur.execute(
                "INSERT INTO operating_systems (full_name, category) VALUES (%s,%s)",
                (os_full, categorize_os(os_full))
            )
            os_id = cur.lastrowid

    custom = {field_map[cv["key"]]: cv["value"]
              for cv in (v.get("customValue") or [])
              if cv.get("key") in field_map}

    env_id   = get_or_create(cur, "environments", "name", custom.get("VI.ENV") or None)
    tier_id  = get_or_create(cur, "tiers",        "name", custom.get("VI.TIER") or None)
    owner_id = get_or_create(cur, "owners",       "name", custom.get("VI.OWNER") or None)

    power = (rt.get("powerState") or "unknown")
    if power not in ("poweredOn","poweredOff","suspended"):
        power = "unknown"

    is_template = 1 if cfg.get("template") else 0

    try:    cpus = int(hw.get("numCPU") or 0) or None
    except: cpus = None
    try:    mem = float(hw.get("memoryMB") or 0) / 1024 or None
    except: mem = None

    now = datetime.now()
    fields = dict(
        name           = name,
        moref          = moref or None,
        hostname       = (guest.get("hostName") or "").strip() or None,
        vcenter_path   = vm_path,
        datacenter_id  = dc_id,
        os_id          = os_id,
        environment_id = env_id,
        tier_id        = tier_id,
        owner_id       = owner_id,
        power_state    = power,
        cpus           = cpus,
        memory_gb      = round(mem, 1) if mem else None,
        purpose        = custom.get("VI.PURPOSE") or None,
        landscape      = custom.get("VI.LANDSCAPE") or None,
        app_name       = custom.get("App_Name") or None,
        description    = custom.get("Description") or None,
        deployment     = custom.get("deployment") or None,
        cmdb_uuid      = custom.get("cmdb_uuid") or None,
        last_seen      = now,
        last_scan_id   = scan_id,
        active         = 1,
        is_template    = is_template,
    )

    # Primary lookup: by stable MoRef within this vCenter (survives renames, works even if dc_id is NULL)
    # Fallback: by name+datacenter (for existing rows without moref)
    existing = None
    if moref and vc_id:
        cur.execute(
            "SELECT n.id, n.name, n.hostname, n.power_state, n.cpus, n.memory_gb, n.os_id, "
            "n.environment_id, n.tier_id, n.owner_id, n.purpose, n.landscape, n.app_name, "
            "n.description, n.deployment, n.cmdb_uuid, n.vcenter_path, n.active, n.is_template "
            "FROM nodes n JOIN datacenters dc ON dc.id=n.datacenter_id "
            "WHERE n.moref=%s AND dc.vcenter_id=%s", (moref, vc_id))
        existing = cur.fetchone()

    if not existing and dc_id:
        cur.execute(
            "SELECT id, name, hostname, power_state, cpus, memory_gb, os_id, "
            "environment_id, tier_id, owner_id, purpose, landscape, app_name, "
            "description, deployment, cmdb_uuid, vcenter_path, active, is_template "
            "FROM nodes WHERE name=%s AND datacenter_id=%s AND (moref IS NULL OR moref='')",
            (name, dc_id))
        existing = cur.fetchone()

    if existing:
        node_id = existing["id"]
        tracked = ['name', 'hostname', 'power_state', 'cpus', 'memory_gb', 'os_id',
                   'environment_id', 'tier_id', 'owner_id', 'purpose',
                   'landscape', 'app_name', 'description', 'deployment',
                   'cmdb_uuid', 'vcenter_path', 'is_template']
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
        fields["first_seen"] = now
        cols         = ", ".join([f"`{k}`" for k in fields])
        placeholders = ", ".join(["%s"] * len(fields))
        try:
            cur.execute(f"INSERT INTO nodes ({cols}) VALUES ({placeholders})",
                        list(fields.values()))
            node_id = cur.lastrowid
        except Exception as _ie:
            # UNIQUE constraint — find by moref and update instead
            if moref and vc_id:
                cur.execute(
                    "SELECT n.id FROM nodes n "
                    "JOIN datacenters dc ON dc.id=n.datacenter_id "
                    "WHERE n.moref=%s AND dc.vcenter_id=%s", (moref, vc_id))
                row = cur.fetchone()
                if row:
                    node_id = row['id']
                    fields.pop('first_seen', None)
                    set_clause = ', '.join([f'`{k}`=%s' for k in fields])
                    cur.execute(f'UPDATE nodes SET {set_clause} WHERE id=%s',
                                list(fields.values()) + [node_id])
                    return node_id
            return None
        cur.execute(
            "INSERT INTO node_history (node_id, field, old_value, new_value, source) "
            "VALUES (%s,'_created',NULL,'1','sync')",
            (node_id,))

    ip = (guest.get("ipAddress") or "").strip()
    if ip:
        cur.execute(
            "INSERT INTO ip_addresses (node_id, ip, is_primary) VALUES (%s,%s,1) "
            "ON DUPLICATE KEY UPDATE is_primary=1",
            (node_id, ip)
        )

    return node_id


def sync_vcenter(env_file, conn, scan_id, workers=20, dry_run=False):
    env         = load_env_file(env_file)
    vcenter_url = env.get("GOVC_URL", env_file)
    cur         = conn.cursor()

    print(f"\n[*] Scanning {vcenter_url}", flush=True)

    datacenter = get_datacenter(env)
    if not datacenter:
        print(f"[!] Could not find datacenter, skipping.", file=sys.stderr)
        return

    field_map = build_field_map(env)

    # Discover all VM paths (recurses into all VM subfolders)
    print(f"    Discovering VMs...", flush=True)
    vm_paths_raw, err = govc_run(env, "find", datacenter + "/vm", "-type", "m")
    if not vm_paths_raw:
        # Fallback: search from datacenter root (handles VMs not in default /vm folder)
        vm_paths_raw, err = govc_run(env, "find", datacenter, "-type", "m")
    if not vm_paths_raw:
        print(f"[!] No VMs found (error: {err[:100]})", file=sys.stderr)
        return

    vm_paths = [p.strip() for p in vm_paths_raw.splitlines() if p.strip()]
    print(f"    Found {len(vm_paths)} VMs — fetching details with {workers} workers...", flush=True)

    if dry_run:
        print(f"    [dry-run] would upsert {len(vm_paths)} VMs from {vcenter_url}")
        return

    # Split into batches for parallel fetching
    batches = [vm_paths[i:i+BATCH_SIZE] for i in range(0, len(vm_paths), BATCH_SIZE)]

    # Phase 1: fetch all VM data in parallel
    all_vm_data = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_vm_batch, env, batch): batch for batch in batches}
        done = 0
        for future in as_completed(futures):
            done += len(futures[future])
            if done % 200 == 0:
                print(f"    {done}/{len(vm_paths)} fetched...", flush=True)
            try:
                all_vm_data.extend(future.result())
            except Exception as e:
                print(f"[!] Batch fetch error: {e}", file=sys.stderr)

    print(f"    Fetched {len(all_vm_data)} VMs — upserting to DB...", flush=True)

    # Phase 2: upsert all to DB sequentially
    seen_node_ids = []
    updated = 0
    errors  = 0

    for vm_path, v in all_vm_data:
        try:
            node_id = upsert_vm(cur, v, vm_path, field_map, vcenter_url, datacenter, scan_id)
            if node_id:
                seen_node_ids.append(node_id)
                updated += 1
        except Exception as e:
            errors += 1
            print(f"[!] Upsert error for {vm_path}: {e}", file=sys.stderr)

        if updated % 200 == 0 and updated > 0:
            conn.commit()

    conn.commit()

    # Mark VMs in this vCenter's datacenter that weren't seen as inactive
    deactivated = 0
    if seen_node_ids:
        placeholders = ",".join(["%s"] * len(seen_node_ids))
        cur.execute(
            f"UPDATE nodes n "
            f"JOIN datacenters dc ON dc.id=n.datacenter_id "
            f"JOIN vcenters vc ON vc.id=dc.vcenter_id "
            f"SET n.active=0 "
            f"WHERE vc.url=%s AND n.id NOT IN ({placeholders}) AND n.active=1",
            [vcenter_url] + seen_node_ids
        )
        deactivated = cur.rowcount
        conn.commit()

    print(f"    Updated/inserted : {updated}", flush=True)
    print(f"    Marked inactive  : {deactivated}", flush=True)
    if errors:
        print(f"    Errors           : {errors}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fast parallel CMDB sync from vCenter")
    parser.add_argument("--env",      nargs="+",
                        default=["~/vcenter_inventory/na1", "~/vcenter_inventory/ev3"])
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=3306)
    parser.add_argument("--user",     default="root")
    parser.add_argument("--password", default="Pay4mysql!")
    parser.add_argument("--db",       default="cmdb")
    parser.add_argument("--workers",  type=int, default=20,
                        help="Parallel govc workers for VM data fetching")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print what would change without writing to DB")
    args = parser.parse_args()

    conn = pymysql.connect(
        host=args.host, port=args.port,
        user=args.user, password=args.password,
        db=args.db, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )
    cur = conn.cursor()
    cur.execute("INSERT INTO scan_runs (source) VALUES (%s)", ("cmdb_sync_fast.py",))
    scan_id = cur.lastrowid
    conn.commit()

    for env_file in args.env:
        try:
            sync_vcenter(env_file, conn, scan_id,
                         workers=args.workers, dry_run=args.dry_run)
        except Exception as e:
            print(f"[!] Error syncing {env_file}: {e}", file=sys.stderr)
            import traceback; traceback.print_exc(file=sys.stderr)

    cur.execute("UPDATE scan_runs SET finished_at=%s WHERE id=%s",
                (datetime.now(), scan_id))
    conn.commit()
    conn.close()
    print(f"\n[+] Fast sync complete (scan_id={scan_id})", flush=True)


if __name__ == "__main__":
    main()
