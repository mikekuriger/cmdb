# CMDB — What It Is and How It Works
### Overview for Teams

---

## What Problem Does It Solve?

Before the CMDB, there was no single place to answer:

- What VMs do we have and where do they live?
- Which ones are in Nagios? Which are being monitored?
- Who owns this machine? What environment is it in?
- When did this VM last change, and what changed?
- Is this thing still alive or was it decommissioned months ago?

The CMDB is the answer to all of these. It is the **single source of truth** for every VM across both data centers.

---

## Scale

| | |
|---|---|
| Active VMs tracked | **5,040** |
| NA1 (Phoenix) | 3,446 |
| EV3 (Chandler) | 1,594 |
| Sync runs completed | 451+ |
| Groups defined | 2,238 |

---

## How Data Gets In

There are two paths:

```
vCenter ──→ cmdb_sync_fast.py ──→ MySQL (automatic, every 30 min)
CSV file ──→ cmdb_import.py   ──→ MySQL (manual / bulk load)
```

The sync script talks directly to vCenter using `govc`, fetches every VM's
configuration and metadata, and writes it to the database. It runs every
30 minutes via cron. A full re-import runs nightly at 2:15am as a backstop.

---

## How Data Gets Out

```
MySQL ──→ REST API ──→ Web UI       (browser — search, filter, manage)
                  ──→ CLI tool      (cmdb_cli.py — scripts, automation)
                  ──→ Ansible       (dynamic inventory via DB view)
                  ──→ Nagios        (nag-gen reads groups from API)
```

Everything reads from the same database through the same API. There is no
separate Nagios list or Ansible inventory file to keep in sync — they all
come from the CMDB.

---

## The Lifecycle of a VM

```
vCenter creates VM
        │
        ▼ (next sync, within 30 min)
CMDB sees it for the first time
        │
        └─ Status set to: SETUP
        └─ Recorded: name, UUID, moref, OS, CPUs, RAM, power state
        └─ node_history entry: _created

Admin reviews and moves to production
        │
        └─ Status changed to: INSERVICE  (via web UI or CLI)
        └─ Added to Nagios and Ansible groups

VM is in use — sync runs every 30 min
        │
        └─ Power state, hostname, OS, vCenter attributes updated automatically
        └─ Every field change written to node_history with timestamp

VM is decommissioned in vCenter
        │
        └─ Next sync: not seen in vCenter → marked INACTIVE
        └─ Disappears from all views, Nagios, Ansible

VM is rebuilt (same name, new hardware)
        │
        └─ Sync detects different UUID → old record retired
        └─ Group memberships copied to new record automatically
        └─ New record starts as SETUP
```

---

## Status Lifecycle

```
SETUP  →  SCREAMTEST  →  INSERVICE  →  OUTOFSERVICE
                                    ↘
                                     RETIRED
```

| Status | Meaning | How Set |
|--------|---------|---------|
| **setup** | Just discovered, not yet in service | Automatic (first sync) |
| **screamtest** | Being tested — name contains "screamtest" | Automatic (name match) |
| **reserved** | Allocated but not yet deployed | Manual |
| **inservice** | Live, in production | Manual |
| **outofservice** | Temporarily offline | Manual |
| **testing** | In a test/QA state | Manual |
| **retired** | Decommissioned | Auto (VM gone from vCenter) or manual |

The sync script **never** changes a status that has already been set — except
to force `screamtest` on name match, or `setup` on first discovery.

---

## How the Sync Recognises a VM

VMs get renamed, moved between folders, and rebuilt. The sync uses a four-level
matching strategy to find the right existing record:

```
1. Hardware UUID   — most reliable, survives renames and vCenter moves
2. moref + vCenter — stable ID within a vCenter
3. moref + DC      — catches VMs originally loaded by CSV import
4. Name + DC       — last resort for hand-entered records with no moref
```

This is why renaming a VM in vCenter just updates the name in the CMDB rather
than creating a duplicate — the moref stays the same even when the name changes.

---

## What the Web UI Provides

- **Nodes page** — searchable, filterable table of all VMs. Filter by site,
  environment, tier, owner, OS, status, group, or free text. Export to CSV.
- **Datacenter / Environment / Tier / Owner pages** — click any to see all
  VMs belonging to it.
- **Groups page** — manage Nagios and Ansible groups. Add/remove members.
- **Statuses page** — see how many VMs are in each lifecycle state.
- **Recent Changes** — audit log of every field change across all VMs.
- **Scan Runs** — history of every sync execution.

---

## What the CLI Provides

```bash
# Find VMs
cmdb_cli.py get --name "web*"
cmdb_cli.py get --status setup
cmdb_cli.py get --group nagios-linux-servers

# Update a VM
cmdb_cli.py set --name web01 --status inservice

# Manage groups
cmdb_cli.py add-to-groups --name web01 --groups my-group
cmdb_cli.py create-groups new-group --nagios --ansible
```

Read operations require no credentials. Write operations require an API token
set in the environment (`CMDB_API_KEY`).

---

## The Nagios and Ansible Connection

```
Admin creates a group in CMDB (is_nagios=1 or is_ansible=1)
        │
        ▼
Admin adds VMs to that group
        │
        ├─ Nagios: nag-gen reads CMDB API → writes hostgroup configs → reload
        └─ Ansible: dynamic inventory script reads CMDB DB view → hosts/groups
```

There are no static inventory files. When a VM is added to a group in the CMDB,
it is in Nagios and Ansible on the next config generation run.

---

## Data Sources for VM Metadata

Most VM metadata comes automatically from vCenter:

| Field | Source |
|-------|--------|
| Name, moref, UUID | vCenter VM identity |
| Hostname | VMware Tools (guest OS reports it) |
| OS | VMware Tools guest detection |
| CPUs, RAM, Power state | vCenter hardware/runtime config |
| Environment, Tier, Owner | vCenter custom attributes (VI.ENV, VI.TIER, VI.OWNER) |
| App name, Purpose | vCenter custom attributes |

This means the CMDB is only as good as the vCenter custom attributes. VMs
with no VI.ENV set will have no environment in the CMDB.

---

## Audit Trail

Every field change is recorded automatically:

```
who changed it    (username for manual, 'sync' for automatic)
what changed      (field name, old value, new value)
when it changed   (timestamp)
```

This covers both manual edits (via UI or CLI) and automatic changes from the
sync (hostname update, OS change, power state flip, etc.). The Recent Changes
page in the UI surfaces this data.

---

## Key Operational Points

- **If a VM isn't showing up:** check whether the sync ran recently (Scan Runs
  page) and whether the VM has a recognisable UUID or moref in vCenter.

- **If a group isn't in Nagios:** confirm the group has `is_nagios=1` and the
  VM is an active member. Then check whether nag-gen has run.

- **If the sync appears stuck:** the lock file `/tmp/cmdb_sync.lock` may be
  stale. It self-clears after 1 hour if the process that created it is gone.

- **Making a bulk change:** use `cmdb_cli.py` with a filter + pipe, or talk
  directly to the REST API. The API accepts standard query parameters for
  filtering before any bulk operation.

---

*na1lnptcmdb-01.corp.pvt — 2026-04-27*
