# CMDB — Infrastructure Asset Manager

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Access](#access)
4. [Web Interface](#web-interface)
   - [Login](#login)
   - [Sidebar Navigation](#sidebar-navigation)
   - [Nodes](#nodes)
   - [Object Detail Pages](#object-detail-pages)
   - [Groups](#groups)
   - [Recent Changes](#recent-changes)
   - [Admin — User Management](#admin--user-management)
5. [Data Sync](#data-sync)
   - [Full Refresh](#full-refresh)
   - [Fast Incremental Sync](#fast-incremental-sync)
   - [Cron Schedule](#cron-schedule)
6. [REST API](#rest-api)
   - [Authentication](#authentication)
   - [Nodes](#nodes-api)
   - [Groups](#groups-api)
   - [Lookup Tables](#lookup-tables-api)
7. [Command Line Tool](#command-line-tool)
   - [Setup](#setup)
   - [Output Formats](#output-formats)
   - [Querying Nodes](#querying-nodes)
   - [Displaying Fields](#displaying-fields)
   - [Updating Nodes](#updating-nodes)
   - [Deleting a Node](#deleting-a-node)
   - [Group Management](#group-management)
   - [Lookup Tables](#lookup-tables)
   - [Scripting Examples](#scripting-examples)
8. [LDAP Configuration](#ldap-configuration)
9. [SSL / HTTPS](#ssl--https)
10. [File Layout](#file-layout)
    - [Production Server](#production-server-na1lnptcmdb-01corpvt)
    - [Dev / Sync Server](#dev--sync-server-u3)

---

## Overview

The CMDB is an internal Infrastructure Asset Manager that stores and displays all VMs from vCenter (both `na1 / PHX1-THRYV-DC` and `ev3 / ev3dccomp01`). It is populated automatically from vCenter via a two-tier sync process and exposed through a web UI, REST API, and command-line tool.

**Capabilities:**
- Browse, search, and filter 5,000+ VMs by name, OS, environment, tier, owner, tag, group, power state, and more
- Click through any object (owner, OS, environment, etc.) to see all associated nodes
- Organize nodes into groups that can be flagged for use by Ansible and/or Nagios
- Admin users can edit node metadata and manage groups through the UI
- A REST API and CLI tool allow scripted queries and automation
- LDAP/Active Directory authentication — users auto-provisioned on first login

---

## Architecture

```
vCenter (na1 + ev3)
        │
        ├─ vcenter_inventory.py   ── full export → CSV (nightly)
        │         │
        │         └─ cmdb_import.py ── CSV → MySQL
        │
        └─ cmdb_sync_fast.py      ── parallel direct scan (every 30 min)
                  │
              MySQL (cmdb)  ← runs on na1lnptcmdb-01
                  │
              Gunicorn (127.0.0.1:5000)
                  │
              nginx (port 443 / HTTPS)  ← public entry point
                  ├─ Web UI  (browser)
                  └─ REST API  (/api/v1/...)
                            │
                        cmdb_cli.py  (command line, run from any host)
```

**Server:** `na1lnptcmdb-01.corp.pvt`  
**Database:** MySQL 8.4, database name `cmdb`, data directory `/mysqldata`  
**Services:** `mysqld`, `cmdb-web` (gunicorn), `nginx` — all enabled and auto-start on boot

---

## Access

| Method | URL |
|---|---|
| Web UI | `https://na1lnptcmdb-01.corp.pvt/` |
| REST API | `https://na1lnptcmdb-01.corp.pvt/api/v1/` |

HTTP (port 80) automatically redirects to HTTPS. The certificate is currently self-signed; browsers will show a one-time warning until a CA-signed cert is installed.

---

## Web Interface

### Login

Navigate to `https://na1lnptcmdb-01.corp.pvt/`. You will be presented with a login page.

- **LDAP (Active Directory)** — log in with your standard AD username (e.g. `mk7193`) and AD password. An account is automatically created in CMDB on first login with read-only access.
- **Local accounts** — username and password managed in the CMDB admin panel. The `admin` local account is used for initial setup and emergency access.

To grant an AD user admin rights: Admin → Users → click the user → check *Admin*.

---

### Sidebar Navigation

The left sidebar provides access to all object types, each showing a live count of records.

| Section | Items |
|---|---|
| **Inventory** | Nodes, IP Addresses, Operating Systems |
| **Metadata** | Environments, Tiers, Owners, Tags, Groups |
| **History** | Recent Changes, Scan Runs |
| **Admin** | Users *(admin only)* |

---

### Nodes

The Nodes page is the primary view. It displays all active VMs in a sortable, paginated table.

**Columns:** Name · Hostname · Power State · OS · Environment · Tier · Owner · Primary IP · Datacenter · Last Seen

**Toolbar:**
- The **search box** filters across name, hostname, IP address, and OS simultaneously — results update as you type
- **All OS / Linux / Windows / Other** — segmented filter button to restrict by OS category
- **Templates only** — toggle to show only VM templates
- **Include inactive** — toggle to include VMs marked inactive (not seen in last sync)
- **Export CSV** — downloads the current filtered view

**Advanced Filters** (click the *Advanced* button):

| Filter | Values |
|---|---|
| Power State | poweredOn, poweredOff, suspended |
| OS Category | linux, windows, other |
| Environment | dropdown populated from data |
| Tier | dropdown populated from data |
| Owner | dropdown populated from data |
| vCenter | dropdown populated from data |
| Tag | free text |
| Group | free text |

The **Include Inactive** toggle shows VMs that were not seen in the last scan.

**Export CSV** — the *Export CSV* button downloads the current filtered view.

**Clicking a row** opens the Node Detail page.

#### Node Detail

Displays all fields for a single VM including:

- Core fields: name, hostname, power state, OS, CPUs, RAM, vCenter path
- Custom attributes: environment, tier, owner, purpose, landscape, app name, description, deployment, CMDB UUID
- IP addresses (all recorded IPs, primary flagged)
- Tags
- Group memberships
- Custom attributes from vCenter

VM templates are marked with a **TMPL** badge in the nodes list and a purple **Template** badge on the detail page.

**Admin edit:** Admins see an *Edit* button that enables inline editing of metadata fields. Changes are saved immediately to the database.

**Delete (admins):** The *Delete* button (next to Edit) opens a confirmation modal and permanently removes the node and all associated records (history, IPs, group memberships, tags, attributes). This cannot be undone.

**Group membership (admins):** The Groups card on the node detail page has an *Add* button (opens a dropdown of all groups) and an ✕ button next to each current group to remove it.

---

### Object Detail Pages

Every object in the sidebar is clickable and leads to a detail page. Each detail page shows:

1. The object's own editable fields (admins only)
2. A full server-side table of all associated nodes, with the same sorting and pagination as the main Nodes view

| Object | Editable Fields |
|---|---|
| Owner | Name, Email |
| Environment | Name |
| Tier | Name |
| Operating System | Category (linux/windows/other), Family |
| Tag | Name, Category |

**Example:** Click on owner *Josh Marceaux* → see all VMs owned by Josh, sortable by power state, OS, datacenter, etc.

Owner email addresses are populated from an AD export file. To refresh after generating a new export, see the **Refresh owner email addresses** script in the [File Layout](#file-layout) section.

---

### Groups

Groups are named collections of nodes. A group can be flagged as used by Ansible, Nagios, both, or neither.

**Group flags:**

| Badge | Meaning |
|---|---|
| `Ansible` (blue) | This group is an Ansible inventory group — included in Ansible inventory exports |
| `Nagios` (yellow) | This group is a Nagios hostgroup — included in Nagios config generation |
| *(no badge)* | Custom/organizational group only |

A single group such as `hadoop` can carry both flags — you do not need two separate groups.

**Groups list page:**
- Filter by Ansible or Nagios using the toggle switches at the top
- Click any row to open the group detail page
- Admins see a delete button per row

**Group detail page:**
- Edit name, description, and Ansible/Nagios flags
- View all member nodes in a sortable table
- **Add Nodes** button opens a modal where you paste node names (one per line or comma-separated). Supports `*` wildcards — e.g. `hadoop*` will match all nodes whose name starts with `hadoop`
- Remove individual nodes with the ✕ button

---

### Recent Changes

The **Recent Changes** page (History → Recent Changes) shows the most recent field-level changes across all nodes.

- Use the **source** dropdown to filter by `sync` (automated) or `manual` (admin edits via UI)
- Use the **field** filter box to focus on a specific field (e.g. `power_state`, `owner`)
- Old values are shown in red with strikethrough; new values in green
- Clicking a row navigates to that node's detail page
- Results default to the last 500 changes; the dropdown adjusts up to 2000

---

### Admin — User Management

Accessible from the sidebar for admin accounts only (`Admin → Users`).

**User types:**

| Type | Description |
|---|---|
| Local | Username + password stored in CMDB |
| LDAP | Authenticated against Active Directory; account auto-created on first login |

**Roles:**

| Role | Capabilities |
|---|---|
| Read-only | Browse all data, use API with read endpoints |
| Admin | Edit nodes, manage groups, create/edit users |

**API Keys:**
Each user has an API key used for CLI and script access. Keys are shown on the user edit page and can be regenerated at any time. A regenerated key immediately invalidates the old one.

---

## Data Sync

### Full Refresh

Run nightly at 2:00 AM by cron on `na1lnptcmdb-01`. Exports all VMs from both vCenters to a CSV then imports the full CSV into MySQL.

**Steps:**
1. `vcenter_inventory.py` — connects to both vCenters via `govc`, exports all VMs with custom attributes and tags to `/tmp/vcenter_inventory.csv`
2. `cmdb_import.py` — reads the CSV and upserts all records into MySQL. VMs not present in the current export are marked `active=0`

**What is captured in the full refresh:**
- All VM fields (power state, OS, CPU, RAM, hostname)
- All custom attributes (VI.ENV, VI.TIER, VI.OWNER, VI.PURPOSE, VI.LANDSCAPE, App_Name, Description, deployment, cmdb_uuid)
- IP addresses
- Tags (if populated in vCenter)

### Fast Incremental Sync

Runs every 30 minutes by cron on `na1lnptcmdb-01`. Uses a direct parallel scan via `govc` — not the vCenter events API, which has reliability issues with large inventories.

**What it does:**
1. Discovers all VM paths in each vCenter datacenter via `govc find`
2. Fetches full VM details in parallel (20 workers, 50 VMs per batch)
3. Upserts every VM into MySQL — detects renames using the stable vCenter `MoRef` ID as the primary key
4. Marks any VM not seen in this run as `active=0`

**Rename detection:** Each VM has a stable `moref` column (`vm-NNNNNN`) that survives renames in vCenter. When a VM is renamed, the sync finds it by MoRef, updates the `name` field, and records the rename in node history — rather than creating a duplicate record.

**Typical runtime:** ~3.5 minutes for 5,000+ VMs across two vCenters.

### Cron Schedule

On `na1lnptcmdb-01`, cron runs as `mk7193`:

```
*/30 * * * *  /home/mk7193/vcenter/cmdb_cron.sh fast  >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
0    2 * * *  /home/mk7193/vcenter/cmdb_cron.sh full  >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
```

Logs: `/home/mk7193/vcenter_inventory/logs/cmdb_sync.log`

A lock file at `/tmp/cmdb_sync.lock` prevents overlapping runs.

---

## REST API

All API endpoints are under `/api/v1/`. All responses are JSON.

### Authentication

Two methods are accepted:

**Session cookie** — log in via the web UI; the session cookie is used automatically by the browser.

**Bearer token** — for scripts and CLI tools:
```
Authorization: Bearer <api_key>
```

API keys are found in Admin → Users → Edit User.

---

### Nodes API

#### `GET /api/v1/nodes`

Returns a paginated list of nodes. Supports DataTables server-side protocol and plain REST.

**Query parameters:**

| Parameter | Description | Match type | Example |
|---|---|---|---|
| `q` | Full-text search (name, hostname, IP, OS) | partial | `q=web01` |
| `active` | `1` (default), `0`, or `all` | exact | `active=all` |
| `power` | Filter by power state | exact | `power=poweredOn` |
| `os_category` | `linux`, `windows`, or `other` | exact | `os_category=linux` |
| `env` | Environment name | partial LIKE | `env=PROD` |
| `tier` | Tier name | partial LIKE | `tier=Production` |
| `owner` | Owner name | partial LIKE | `owner=kuriger` |
| `tag` | Tag name | partial LIKE | `tag=kubernetes` |
| `group` | Group name | partial LIKE | `group=hadoop` |
| `os_id` | OS ID (used by OS detail page) | exact | `os_id=17` |
| `env_id` | Environment ID | exact | `env_id=5` |
| `tier_id` | Tier ID | exact | `tier_id=2` |
| `owner_id` | Owner ID | exact | `owner_id=42` |
| `tag_id` | Tag ID | exact | `tag_id=8` |
| `limit` | Max records to return | — | `limit=100` |
| `offset` | Pagination offset | — | `offset=100` |
| `sort` | Sort column | — | `sort=last_seen` |
| `dir` | `asc` or `desc` | — | `dir=desc` |

**Response:**
```json
{
  "data": [ { "id": 4965, "name": "018Client03", "power_state": "poweredOn", ... } ],
  "total": 5128
}
```

#### `GET /api/v1/nodes/<id>`

Returns full detail for a single node including IP addresses, groups, and custom attributes.

#### `PUT /api/v1/nodes/<id>` *(admin)*

Update editable fields on a node.

```json
{ "purpose": "Web server", "owner_id": 42 }
```

Allowed fields: `purpose`, `landscape`, `app_name`, `description`, `deployment`, `cmdb_uuid`, `owner_id`, `environment_id`, `tier_id`

#### `DELETE /api/v1/nodes/<id>` *(admin)*

Permanently deletes a node and all associated records: change history, IP addresses, group memberships, tags, and custom attributes. There is no soft-delete — this removes the row from the database entirely.

---

### Groups API

#### `GET /api/v1/groups`

| Parameter | Description |
|---|---|
| `ansible=1` | Only Ansible inventory groups |
| `nagios=1` | Only Nagios hostgroups |
| `q=text` | Search by name |

#### `POST /api/v1/groups` *(admin)*

```json
{ "name": "hadoop", "is_ansible": 1, "is_nagios": 1, "description": "Hadoop cluster nodes" }
```

#### `PUT /api/v1/groups/<id>` *(admin)*

```json
{ "name": "hadoop", "is_ansible": 1, "is_nagios": 0, "description": "Updated" }
```

#### `DELETE /api/v1/groups/<id>` *(admin)*

Deletes the group. Nodes are not affected.

#### `GET /api/v1/groups/<id>/nodes`

Returns all member nodes of a group.

#### `POST /api/v1/groups/<id>/nodes` *(admin)*

Add nodes by name pattern or ID list.

```json
{ "names": ["hadoop*", "namenode01", "datanode01,datanode02"] }
```

```json
{ "ids": [1234, 1235, 1236] }
```

Supports `*` and `?` wildcards. Response includes count of matched and added nodes, plus any patterns that matched nothing.

#### `DELETE /api/v1/groups/<id>/nodes` *(admin)*

Remove nodes by name pattern or ID list. Same body format as POST.

---

### Lookup Tables API

All return `{ "data": [...] }`.

| Endpoint | Description |
|---|---|
| `GET /api/v1/os` | Operating systems with node counts |
| `GET /api/v1/environments` | Environments with node counts |
| `GET /api/v1/tiers` | Tiers with node counts |
| `GET /api/v1/owners` | Owners with node counts |
| `GET /api/v1/tags` | Tags with node counts |
| `GET /api/v1/ips` | IP addresses (`?q=`, `?node_id=`) |
| `GET /api/v1/vcenters` | vCenter instances |
| `GET /api/v1/scan-runs` | Last 100 sync runs |
| `GET /api/v1/changes` | Recent node field-level changes (`?limit=500`, max 2000) |
| `GET /api/v1/counts` | Sidebar counts for all object types |

Each lookup object also has `GET /api/v1/<type>/<id>` and `PUT /api/v1/<type>/<id>` endpoints.

---

## Command Line Tool

The CLI tool (`cmdb_cli.py`) lives on `na1lnptcmdb-01` at `~/cmdb_cli.py`. It can be copied to any machine that can reach the production server.

The interface uses flag-based syntax (similar to nVentory's `nv` tool) — `--get`, `--exactget`, `--name`, `--set`, `--delete`, and `--addtonodegroup` rather than subcommands.

### Setup

```bash
export CMDB_URL=https://na1lnptcmdb-01.corp.pvt
export CMDB_API_KEY=<your api key from Admin → Users → Edit>
```

Add these to your `~/.bashrc` or `~/.bash_profile` for persistence.

You can also pass them inline:
```bash
python3 ~/cmdb_cli.py --url https://na1lnptcmdb-01.corp.pvt --key <api_key> --name web01
```

---

### Output Formats

The `-o` / `--output` flag controls output format:

```bash
python3 ~/cmdb_cli.py --name web01                    # table (default)
python3 ~/cmdb_cli.py -o json --name web01            # JSON
python3 ~/cmdb_cli.py -o csv --get os=linux > out.csv # CSV
```

---

### Querying Nodes

With no `--fields` or `--allfields`, `--get`/`--name` prints one name per line.
Add `--allfields` or `--fields` to see columns.

#### Look up a node by name (partial match)
```bash
python3 ~/cmdb_cli.py --name web01
python3 ~/cmdb_cli.py --name web01 --allfields
```

#### Exact name match
```bash
python3 ~/cmdb_cli.py --exactget name=web01.corp.pvt
```

#### Search by any field
```bash
# --get does a substring/partial match
python3 ~/cmdb_cli.py --get os=linux
python3 ~/cmdb_cli.py --get env=PROD
python3 ~/cmdb_cli.py --get owner=kuriger
python3 ~/cmdb_cli.py --get power=poweredOn
python3 ~/cmdb_cli.py --get tag=kubernetes
python3 ~/cmdb_cli.py --get group=hadoop
python3 ~/cmdb_cli.py --get datacenter=PHX1
```

#### Combine multiple filters (all must match — AND logic)
```bash
python3 ~/cmdb_cli.py --get os=linux --get env=PROD --get power=poweredOn
python3 ~/cmdb_cli.py --get owner=sherpa --get os=linux --allfields
```

#### Include inactive nodes
```bash
python3 ~/cmdb_cli.py --get env=PROD --all
```

#### Limit results
```bash
python3 ~/cmdb_cli.py --get os=linux --limit 50
```

---

### Displaying Fields

Use `--fields` to control which columns are shown, or `--allfields` for the full default set.

```bash
# Show specific fields for a node
python3 ~/cmdb_cli.py --name web01 --fields hostname,power_state,owner

# Show group memberships
python3 ~/cmdb_cli.py --name web01 --fields name,groups

# Show just the name (useful for scripting)
python3 ~/cmdb_cli.py --get os=linux --get env=PROD --fields name

# Full wide table
python3 ~/cmdb_cli.py --get env=PROD --allfields
```

**Available field names for `--fields`:**

| Field | Description |
|---|---|
| `name` | Node name |
| `hostname` | Hostname |
| `power` / `power_state` | Power state |
| `os` / `os_category` | OS category (linux/windows/other) |
| `env` / `environment` | Environment |
| `tier` | Tier |
| `owner` | Owner name |
| `ip` / `primary_ip` | Primary IP address |
| `datacenter` | Datacenter path |
| `groups` / `node_groups` | Group memberships (comma-separated) |
| `tags` | Tags (comma-separated) |
| `template` / `is_template` | Whether this is a VM template |
| `cpus` | CPU count |
| `memory_gb` | RAM in GB |
| `purpose`, `app_name`, `description` | Custom metadata fields |
| `last_seen`, `first_seen` | Sync timestamps |

> **Note:** `--fields groups` requires one extra API call per node. For large result sets (>50 nodes), use `--objecttype groups --shownodes` instead.

---

### Updating Nodes

`--set FIELD=VALUE` updates matched nodes. Combine with `--name` or `--get` to select targets.

```bash
# Set owner on a single node
python3 ~/cmdb_cli.py --name web01 --set owner=kuriger --yes

# Set multiple fields at once
python3 ~/cmdb_cli.py --name web01 --set purpose="Web server" --set tier=Production --yes

# Bulk update: set environment on all nodes matching a filter
python3 ~/cmdb_cli.py --get env=DEV --set environment=Development --yes

# Preview what would change without writing
python3 ~/cmdb_cli.py --get owner=unknown --set owner=kuriger --dry-run
```

**Settable fields:**

| Field | Notes |
|---|---|
| `name`, `hostname` | Direct string fields |
| `purpose`, `landscape`, `app_name`, `description`, `deployment`, `cmdb_uuid` | Custom metadata |
| `owner` | Looked up by name in the owners table |
| `environment` / `env` | Looked up by name |
| `tier` | Looked up by name |

---

### Deleting a Node

`--delete` permanently removes matched nodes. Intended for cleaning up stale records (pre-rename duplicates, decommissioned VMs).

```bash
# With confirmation prompt
python3 ~/cmdb_cli.py --name old-vm-name --delete

# Skip confirmation (for scripts)
python3 ~/cmdb_cli.py --name old-vm-name --delete --yes

# Dry run first
python3 ~/cmdb_cli.py --get env=DECOMMISSIONED --delete --dry-run
```

> **Note:** If the VM still exists in vCenter, the next sync will re-insert it. Only delete records for VMs that are truly gone.

---

### Group Management

#### List all groups
```bash
python3 ~/cmdb_cli.py --objecttype groups
python3 ~/cmdb_cli.py --objecttype groups --get name=hadoop
```

#### Show members of a group
```bash
python3 ~/cmdb_cli.py --objecttype groups --exactget name=hadoop --shownodes
python3 ~/cmdb_cli.py -o csv --objecttype groups --exactget name=hadoop --shownodes > hadoop.csv
```

#### Create a group
```bash
python3 ~/cmdb_cli.py --createnodegroup hadoop --ansible --nagios --desc "Hadoop cluster"
python3 ~/cmdb_cli.py --createnodegroup decommission-q3 --desc "To be decommissioned Q3"
```

#### Add nodes to a group
```bash
# By exact name
python3 ~/cmdb_cli.py --name namenode01 --addtonodegroup hadoop

# By pattern (partial match)
python3 ~/cmdb_cli.py --name hadoop --addtonodegroup hadoop-prod

# Add to multiple groups at once
python3 ~/cmdb_cli.py --name "web*" --addtonodegroup web-prod,ansible-web --yes
```

#### Remove nodes from a group
```bash
python3 ~/cmdb_cli.py --name old-node --removefromnodegroup hadoop --yes
python3 ~/cmdb_cli.py --get env=DECOMMISSIONED --removefromnodegroup prod-web --yes
```

#### Delete a group
```bash
# With confirmation
python3 ~/cmdb_cli.py --objecttype groups --exactget name=old-group --delete

# Skip confirmation
python3 ~/cmdb_cli.py --objecttype groups --exactget name=old-group --delete --yes

# Delete multiple groups
python3 ~/cmdb_cli.py --objecttype groups --get name=temp- --delete --yes
```

---

### Lookup Tables

```bash
python3 ~/cmdb_cli.py --lookup os            # operating systems with node counts
python3 ~/cmdb_cli.py --lookup environments  # all environments
python3 ~/cmdb_cli.py --lookup tiers         # all tiers
python3 ~/cmdb_cli.py --lookup owners        # all owners with email + node count
python3 ~/cmdb_cli.py --lookup tags          # all tags
python3 ~/cmdb_cli.py --lookup ips           # all IP addresses
python3 ~/cmdb_cli.py --scan-runs            # recent sync history (shortcut)
python3 ~/cmdb_cli.py --lookup vcenters      # vCenter instances
```

All support `-o json` and `-o csv`.

---

### Scripting Examples

**Names of all Ansible groups:**
```bash
python3 ~/cmdb_cli.py --objecttype groups --get ansible=1 --fields name
```

**Powered-on Linux nodes in JSON (e.g. for Ansible dynamic inventory):**
```bash
python3 ~/cmdb_cli.py -o json --get os=linux --get power=poweredOn
```

**Export all nodes for a given owner to CSV:**
```bash
python3 ~/cmdb_cli.py -o csv --get owner=kuriger > nodes.csv
```

**Show groups for a specific host:**
```bash
python3 ~/cmdb_cli.py --name web01 --fields name,groups
```

**Bulk-add nodes listed in a file to a group:**
```bash
# nodes.txt has one name per line
while read node; do
  python3 ~/cmdb_cli.py --name "$node" --addtonodegroup my-group --yes
done < nodes.txt
```

---

## LDAP Configuration

LDAP authentication is enabled on the production server and configured to authenticate against the Thryv Active Directory.

**Current configuration (on `na1lnptcmdb-01`):**

| Setting | Value |
|---|---|
| Server | `dcpaddsc04.corp.pvt:389` |
| Bind DN | `CN=svc_ussinf,OU=Service Accounts,DC=corp,DC=pvt` |
| Base DN | `OU=Active,OU=Users,OU=NA1,DC=corp,DC=pvt` |
| User filter | `(sAMAccountName={username})` |
| Email attribute | `mail` |

Configuration is set via environment variables in `/etc/systemd/system/cmdb-web.service`. To change settings:

```bash
sudo vi /etc/systemd/system/cmdb-web.service
sudo systemctl daemon-reload
sudo systemctl restart cmdb-web
```

LDAP users are auto-provisioned in the local users table on first login with read-only access. An admin must grant admin role if needed via Admin → Users.

**To disable LDAP** (e.g. for troubleshooting), set `LDAP_ENABLED=false` in the service file and restart.

---

## SSL / HTTPS

nginx terminates HTTPS on port 443. HTTP (port 80) redirects to HTTPS automatically.

**Certificate location:**
```
/etc/nginx/ssl/cmdb.crt   # certificate
/etc/nginx/ssl/cmdb.key   # private key
```

The current certificate is **self-signed** (valid 10 years). Browsers will show a one-time security warning.

**To install a CA-signed certificate** (e.g. from an internal CA):
```bash
# Copy new cert and key to the server
scp your.crt na1lnptcmdb-01.corp.pvt:/tmp/
scp your.key na1lnptcmdb-01.corp.pvt:/tmp/

# On the server
sudo cp /tmp/your.crt /etc/nginx/ssl/cmdb.crt
sudo cp /tmp/your.key /etc/nginx/ssl/cmdb.key
sudo nginx -t && sudo systemctl reload nginx
```

---

## File Layout

### Production Server (`na1lnptcmdb-01.corp.pvt`)

```
/home/mk7193/
├── cmdb_cli.py                     # Command line tool
│
├── cmdb_web/                       # Web application
│   ├── app.py                      # Flask application factory
│   ├── config.py                   # Configuration (DB, LDAP, secret key)
│   ├── db.py                       # MySQL connection helpers
│   ├── auth.py                     # Login, LDAP, API key auth, decorators
│   ├── views.py                    # Web page routes
│   ├── api.py                      # REST API routes (/api/v1/...)
│   ├── wsgi.py                     # Gunicorn entry point
│   ├── manage.py                   # Management commands (createuser, listusers)
│   ├── requirements.txt            # Python dependencies
│   └── templates/
│       ├── base.html               # Layout with sidebar
│       ├── login.html
│       ├── nodes.html              # Main nodes table
│       ├── node_detail.html        # Single node view
│       ├── groups.html             # Groups list
│       ├── group_detail.html       # Group detail + member nodes
│       ├── object_detail.html      # Reusable detail page (owner/os/env/tier/tag)
│       ├── simple_table.html       # Generic table (IPs, scan runs)
│       └── admin/
│           ├── users.html
│           └── user_form.html
│
└── vcenter_inventory/              # vCenter sync scripts
    ├── vcenter_inventory.py        # Full vCenter export → CSV
    ├── cmdb_import.py              # CSV → MySQL import
    ├── cmdb_sync_fast.py           # Parallel direct sync (every 30 min)
    ├── cmdb_cron.sh                # Cron wrapper (fast + full modes)
    ├── na1                         # govc env file for PHX1 vCenter (na1 / PHX1-THRYV-DC)
    ├── ev3                         # govc env file for ev3 vCenter (ev3dccomp01)
    └── logs/
        └── cmdb_sync.log           # Sync run logs

/mysqldata/                         # MySQL data directory (symlinked from /var/lib/mysql)

/etc/nginx/
├── conf.d/cmdb.conf                # nginx virtual host (HTTP redirect + HTTPS proxy)
└── ssl/
    ├── cmdb.crt                    # SSL certificate
    └── cmdb.key                    # SSL private key

/etc/systemd/system/
└── cmdb-web.service                # Gunicorn systemd service (includes all env vars)
```

**Cron jobs** (runs as `mk7193`):
```
*/30 * * * *  /home/mk7193/vcenter_inventory/cmdb_cron.sh fast >> /home/mk7193/vcenter_inventory/logs/cmdb_sync.log 2>&1
0    2 * * *  /home/mk7193/vcenter_inventory/cmdb_cron.sh full >> /home/mk7193/vcenter_inventory/logs/cmdb_sync.log 2>&1
```

**Service management:**
```bash
sudo systemctl restart cmdb-web      # restart gunicorn
sudo systemctl reload nginx          # reload nginx config (no downtime)
sudo journalctl -u cmdb-web -f       # live app logs
sudo journalctl -u nginx -f          # live nginx logs
sudo journalctl -u mysqld -f         # live MySQL logs
```

**Manual sync:**
```bash
~/vcenter_inventory/cmdb_cron.sh full                              # run a full sync now
~/vcenter_inventory/cmdb_cron.sh fast                              # run a fast incremental sync now
python3 ~/vcenter_inventory/cmdb_sync_fast.py --dry-run            # fast sync without DB writes
```

**User management:**
```bash
cd ~/cmdb_web
python3 manage.py createuser <username> --password <pw> [--admin] [--email user@corp.pvt]
python3 manage.py listusers
```

**Refresh owner email addresses** (after generating a new AD export):
```bash
# Copy updated file to /home/mk7193/email_addresses.txt then run:
python3 - <<'EOF'
import pymysql
pairs = []
name = None
with open('/home/mk7193/email_addresses.txt') as f:
    for line in f:
        line = line.strip()
        if line.startswith('displayName:'):
            name = line[len('displayName:'):].strip()
        elif line.startswith('userPrincipalName:') and name:
            pairs.append((name, line[len('userPrincipalName:'):].strip()))
            name = None

def normalize(d):
    if ',' in d:
        p = d.split(',', 1)
        return p[1].strip() + ' ' + p[0].strip()
    return d

email_map = {normalize(n).lower(): e for n, e in pairs}
conn = pymysql.connect(host='127.0.0.1', user='root', password='Pay4mysql!',
                       database='cmdb', cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()
cur.execute('SELECT id, name FROM owners')
updated = 0
for o in cur.fetchall():
    e = email_map.get(o['name'].lower())
    if e:
        cur.execute('UPDATE owners SET email=%s WHERE id=%s', (e, o['id']))
        updated += 1
conn.commit()
print(f'Updated {updated} emails.')
conn.close()
EOF
```

---

### Dev Reference (`u3`)

`u3` is no longer a dependency. It retains a dev copy of the app and the original sync scripts for reference, but all production workloads run on `na1lnptcmdb-01`.
