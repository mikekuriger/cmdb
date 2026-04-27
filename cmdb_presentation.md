# CMDB: Architecture, Sync Logic & Decision Making
### Internal Reference — Unix Administration Team

---

## 1. What Is the CMDB?

A **Configuration Management Database** that tracks every VM across two vCenter environments:

| Site | vCenter Label | Active VMs |
|------|--------------|-----------|
| Phoenix / NA1 | `phx1` — PHX1-THRYV-DC | 3,446 |
| Chandler / EV3 | `ev3` — ev3dccomp01 | 1,594 |
| **Total** | | **5,040** |

Data is pulled directly from vCenter every 30 minutes. The DB is the authoritative source for Nagios monitoring groups, Ansible inventory, asset lifecycle status, and VM metadata.

---

## 2. Component Map

```
vCenter (govc)
     │
     │  govc vm.info -json (parallel, 50 VMs/batch)
     ▼
cmdb_sync_fast.py  ──── every 30 min (cron)
cmdb_import.py     ──── CSV import / bulk load
     │
     ▼
MySQL: cmdb database (na1lnptcmdb-01)
     │
     ├── Flask REST API  (/api/v1/*)
     │        │
     │        ├── Web UI  (browser)
     │        └── cmdb_cli.py  (command line)
     │
     └── v_ansible view  (Ansible dynamic inventory)
         v_nodes view     (reporting)
```

---

## 3. Database Schema — Key Tables

### `nodes` — one row per VM (or physical host)
```
id            — internal auto-increment PK
name          — VM display name from vCenter
moref         — vCenter Managed Object Reference (e.g. "vm-1234")
vm_uuid       — VMware hardware UUID (config.uuid) — survives renames
hostname      — guest FQDN from VMware Tools
datacenter_id — FK → datacenters
vcenter_id    — FK → vcenters
os_id         — FK → operating_systems
environment_id, tier_id, owner_id — FK to lookup tables
power_state   — poweredOn / poweredOff / suspended / unknown
cpus, memory_gb
status_id     — FK → statuses (lifecycle state)
active        — 1 = present in vCenter, 0 = gone/retired
is_template   — 1 = vCenter template (excluded from normal queries)
first_seen, last_seen, last_scan_id
```

### Lookup tables (all normalised)
```
statuses        — inservice, setup, screamtest, reserved, retired, ...
environments    — populated from vCenter custom attribute VI.ENV
tiers           — from VI.TIER
owners          — from VI.OWNER
datacenters     — one row per vCenter datacenter object
operating_systems — normalised OS strings + category (linux/windows/other)
groups_         — Nagios/Ansible groups, manually managed
group_members   — node ↔ group many-to-many
node_history    — every field change, timestamped, with source
scan_runs       — one row per sync execution
```

---

## 4. The Sync Script — cmdb_sync_fast.py

### Schedule
```
*/30 * * * *   cmdb_cron.sh fast   → cmdb_sync_fast.py
15   2 * * *   cmdb_cron.sh full   → full nightly re-import via cmdb_import.py
```

### Lock File
Before doing anything, the script checks `/tmp/cmdb_sync.lock`:

```
Lock exists?
├── Age < 1 hour  → another instance running → EXIT immediately
└── Age ≥ 1 hour  → stale? → check if recorded PID is alive
    ├── PID alive  → long-running sync still in progress → EXIT
    └── PID dead   → process was killed without cleanup → DELETE lock, continue
```

Lock contains the PID of the running process. Released via `finally:` block so crashes clean up automatically.

### Execution Flow

```
1. acquire_lock()
2. INSERT scan_runs row → get scan_id
3. For each vCenter (NA1, EV3):
   a. load_env_file()      — loads GOVC_URL, credentials
   b. get_datacenter()     — govc ls /  → top-level DC name
   c. build_field_map()    — maps vCenter custom attribute IDs → names
   d. govc find .../vm -type m  → list of all VM paths (~5000 paths)
   e. Split into batches of 50, fetch in parallel (20 threads default)
      └── govc vm.info -json <batch>  → full VM config + runtime + guest
   f. Upsert all VMs to DB sequentially (upsert_vm)
   g. Deactivation sweep
4. UPDATE scan_runs finished_at
5. release_lock()
```

---

## 5. VM Lookup — How the Script Finds an Existing Record

This is the most critical logic. The script tries four methods in order of reliability:

```python
# Priority 1: hardware UUID (most stable — survives renames, vCenter moves)
SELECT ... FROM nodes WHERE vm_uuid = ?

# Priority 2: moref + vcenter_id (stable within a vCenter, fast)
SELECT ... FROM nodes WHERE moref = ? AND vcenter_id = ?

# Priority 3: moref + datacenter_id (handles CSV-imported records with no vcenter_id)
SELECT ... FROM nodes WHERE moref = ? AND datacenter_id = ?

# Priority 4: name + datacenter, no moref (pre-sync manual entries)
SELECT ... FROM nodes WHERE name = ? AND datacenter_id = ?
           AND (moref IS NULL OR moref = '')
```

**Why four levels?**  
CSV imports (cmdb_import.py) don't set `vcenter_id` — only `datacenter_id`. So Priority 2 would miss those records and the sync would try to insert a duplicate. Priority 3 catches this case.

**Rename handling:**  
`moref` is stable even when a VM is renamed in vCenter. So renaming `ev3lnpgearman01` → `ev3lnpgearmn-01` is handled by Priority 2/3 — the same moref is found, and the `name` field is updated in the DB automatically on the next sync.

---

## 6. Status Assignment Logic

Status is set on every upsert:

```python
if 'screamtest' in name.lower():
    fields['status_id'] = 5          # screamtest — name contains the word
elif existing record found:
    fields['status_id'] = existing['status_id']   # preserve whatever is set
else:
    fields['status_id'] = 6          # setup — brand new VM, never seen before
```

| Condition | Status | ID |
|-----------|--------|----|
| Name contains "screamtest" (case-insensitive) | screamtest | 5 |
| VM already in DB | unchanged | — |
| First time seen | setup | 6 |

**Status IDs for reference:**
```
1 = inservice      5 = screamtest
2 = outofservice   6 = setup
3 = reserved       7 = testing
4 = retired        8 = unknown
```

Statuses are manually changed by admins via the web UI or CLI after a VM moves through its lifecycle. The sync script never downgrades a status — it only sets it on first discovery or forces `screamtest`.

---

## 7. VM Replacement Detection

When a VM is rebuilt (same name, new hardware UUID), the sync detects this automatically:

```
New VM arrives with:
  name = "appserver-01"
  vm_uuid = "4207f41a-..."   ← different UUID

Lookup chain finds nothing (new UUID, no moref match).

Before INSERT — check for replacement:
  SELECT id FROM nodes
  WHERE name = 'appserver-01'
    AND datacenter_id = <same DC>
    AND vm_uuid IS NOT NULL
    AND vm_uuid != '4207f41a-...'   ← different UUID
    AND active = 1

If found → OLD node:
  UPDATE nodes SET active=0, status_id=4 WHERE id=<old_id>   ← retired
  Copy all group memberships to new node
  INSERT node_history (_replaced, old_id → new_id)

INSERT new node → status_id=6 (setup)
```

This handles the "server rebuild" case without losing Nagios/Ansible group assignments.

---

## 8. Deactivation Sweep

After all VMs are upserted, any node belonging to this vCenter that was NOT seen in this scan is marked inactive:

```sql
UPDATE nodes
SET active = 0
WHERE vcenter_id = <vc_id>
  AND id NOT IN (<all node IDs seen this run>)
  AND active = 1
```

This uses `vcenter_id` directly (no JOIN), which means it catches even nodes whose `datacenter_id` is NULL — avoiding the silent-miss bug that would leave stale records marked active.

Inactive nodes (`active=0`) are:
- Hidden from all UI views and API results by default
- Not included in Nagios or Ansible inventory
- Not counted in any metadata statistics
- Visible only when "Show inactive" is explicitly toggled on the Nodes page

---

## 9. Custom Attributes — vCenter → CMDB Mapping

The sync reads vCenter custom attributes and maps them to CMDB fields:

| vCenter Attribute | CMDB Field | Table |
|-------------------|-----------|-------|
| VI.ENV | environment | environments |
| VI.TIER | tier | tiers |
| VI.OWNER | owner | owners |
| VI.PURPOSE | purpose | nodes.purpose |
| VI.LANDSCAPE | landscape | nodes.landscape |
| App_Name | app_name | nodes.app_name |
| Description | description | nodes.description |
| deployment | deployment | nodes.deployment |
| cmdb_uuid | cmdb_uuid | nodes.cmdb_uuid |

`get_or_create()` is used for environment/tier/owner — if a value appears that hasn't been seen before, a new row is inserted automatically. This is why the Environments list can grow when admins add new VI.ENV values in vCenter.

---

## 10. Change History

Every field change is recorded in `node_history`:

```sql
CREATE TABLE node_history (
  id          INT UNSIGNED AUTO_INCREMENT,
  node_id     INT UNSIGNED,
  field       VARCHAR(64),     -- field name, or '_created', '_replaced'
  old_value   TEXT,
  new_value   TEXT,
  source      VARCHAR(64),     -- 'sync', 'manual', 'import'
  changed_by  VARCHAR(128),    -- username for manual changes
  changed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

Special field values:
- `_created` — first time a node was inserted
- `_replaced` — node was retired because a replacement was detected (old_value=old_id, new_value=new_id)

451 sync runs have been recorded since 2026-04-14.

---

## 11. Templates

vCenter templates (`config.template = true`) are stored in the `nodes` table with `is_template=1`.

Rules:
- Templates are **excluded** from all node counts, API results, and list views by default
- Templates have **no** status, owner, tier, environment, or group memberships — these fields are NULL
- A "Show templates" toggle on the Nodes page reveals them
- The sync updates template metadata (name, moref, OS) but never sets status or classification fields

Why store them at all? Templates appear in `govc find` output. Excluding them from the DB would require maintaining a separate ignore list. Storing with `is_template=1` is cleaner.

---

## 12. The CLI — cmdb_cli.py

```bash
# Read — no token required
cmdb_cli.py get --name web*
cmdb_cli.py get --status setup --env production
cmdb_cli.py get --group nagios-linux-servers
cmdb_cli.py get --datacenter EV3

# Write — requires CMDB_API_KEY or --key
cmdb_cli.py set --name web01 --status inservice
cmdb_cli.py add-to-groups --name web01 --groups nagios-web-servers
cmdb_cli.py remove-from-groups --name web01 --groups old-group
cmdb_cli.py create-groups mygroup --nagios --ansible
```

Environment variables:
```bash
export CMDB_URL=https://na1lnptcmdb-01.corp.pvt
export CMDB_API_KEY=<token>
```

The CLI talks to the REST API. GET endpoints are public (no token). PUT/POST/DELETE require a Bearer token. Tokens are per-user and managed in the Admin → Users section of the web UI.

---

## 13. REST API — Key Endpoints

| Method | Endpoint | Auth | Description |
|--------|---------|------|-------------|
| GET | /api/v1/nodes | none | List/search nodes (DataTables + REST) |
| GET | /api/v1/nodes/:id | none | Node detail + IPs + groups + attrs |
| PUT | /api/v1/nodes/:id | token | Update node fields |
| DELETE | /api/v1/nodes/:id | token | Delete node |
| GET | /api/v1/statuses | none | Status list with counts |
| GET | /api/v1/groups | none | Group list |
| POST | /api/v1/groups | token | Create group |
| POST | /api/v1/nodes/:id/groups | token | Add node to groups |
| DELETE | /api/v1/nodes/:id/groups | token | Remove node from groups |
| GET | /api/v1/counts | none | Sidebar counts |

Node query parameters (all combinable):
```
?q=          full-text search (name, hostname, IP, OS)
?status=     partial match on status name
?status_id=  exact status by ID
?datacenter_id=  filter by datacenter
?env=        partial match on environment
?owner=      partial match on owner
?group=      exact group name
?active=     1 (default) | 0 | all
?is_template=0 (default) | 1
?limit=25    pagination
?sort=name   sort field
```

---

## 14. Ansible & Nagios Integration

Two MySQL views are defined for direct consumption:

**`v_ansible`** — used by Ansible dynamic inventory scripts. Joins nodes → groups → group_members and returns only `is_ansible=1` groups with their active, non-template members.

**`v_nodes`** — flattened view of nodes with all lookups resolved (environment name, tier name, etc.) for reporting queries.

Group flags on the `groups_` table:
```
is_ansible = 1  → group appears in Ansible inventory
is_nagios  = 1  → group is used as a Nagios hostgroup
```

A VM gets into Nagios by being added to a group where `is_nagios=1`. The CMDB does not generate Nagios config directly — a separate `nag-gen` process reads the CMDB API and writes hostgroup configs.

---

## 15. Operational Notes

**If the sync seems stuck:**
```bash
cat /tmp/cmdb_sync.lock          # shows PID
ps -p <PID>                      # is it still running?
# If dead:
rm /tmp/cmdb_sync.lock           # next run will proceed automatically
# (or wait — lock is auto-cleared after 1 hour if PID is gone)
```

**Sync log:**
```bash
tail -f /home/mk7193/vcenter_inventory/logs/cmdb_sync.log
```

**Manual sync run:**
```bash
cd /home/mk7193/vcenter_inventory
python3 cmdb_sync_fast.py --dry-run   # show what would change
python3 cmdb_sync_fast.py             # live run
```

**To update a node's status after a rebuild:**
```bash
cmdb_cli.py set --name <hostname> --status inservice
```

**If a renamed VM isn't updating:**  
The script matches on moref first. If the original record was CSV-imported (no vcenter_id), it falls back to `moref + datacenter_id`. If both fail (e.g., record has no moref at all), it falls back to name+datacenter. The history table will show what happened.

---

*Generated 2026-04-27 — na1lnptcmdb-01.corp.pvt*
