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
   - [Querying Nodes](#querying-nodes)
   - [Group Management](#group-management)
   - [Lookup Commands](#lookup-commands)
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
        └─ cmdb_sync_fast.py      ── event-based incremental sync (every 30 min)
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
| **History** | Scan Runs |
| **Admin** | Users *(admin only)* |

---

### Nodes

The Nodes page is the primary view. It displays all active VMs in a sortable, paginated table.

**Columns:** Name · Hostname · Power State · OS · Environment · Tier · Owner · Primary IP · Datacenter · Last Seen

**Searching:**
- The search box at the top filters across name, hostname, IP address, and OS simultaneously
- Results update as you type

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

**Admin edit:** Admins see an *Edit* button that enables inline editing of metadata fields. Changes are saved immediately to the database.

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

Runs every 30 minutes by cron on `na1lnptcmdb-01`. Polls the vCenter events API for changes since the last run and updates only the affected VMs.

**Events detected:**

| Event | Action |
|---|---|
| VmPoweredOnEvent, VmPoweredOffEvent, VmSuspendedEvent | Update power state |
| VmCreatedEvent, VmClonedEvent, VmRegisteredEvent, VmDeployedEvent | Insert or update VM |
| VmReconfiguredEvent | Refresh all fields (CPU/RAM may have changed) |
| VmRenamedEvent | Update name |
| VmRemovedEvent, VmUnregisteredEvent | Mark VM as `active=0` |

**Not detected by fast sync** (caught by nightly full refresh):
- Tag changes
- Custom attribute changes without a reconfigure event
- IP changes without a reconfigure event

### Cron Schedule

On `na1lnptcmdb-01`, cron runs as `mk7193`:

```
*/30 * * * *  /home/mk7193/vcenter/cmdb_cron.sh fast  >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
0    2 * * *  /home/mk7193/vcenter/cmdb_cron.sh full  >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
```

Logs: `/home/mk7193/vcenter/logs/cmdb_sync.log`

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
| `GET /api/v1/counts` | Sidebar counts for all object types |

Each lookup object also has `GET /api/v1/<type>/<id>` and `PUT /api/v1/<type>/<id>` endpoints.

---

## Command Line Tool

The CLI tool (`cmdb_cli.py`) lives on `na1lnptcmdb-01` at `~/cmdb_cli.py`. It can be copied to any machine that can reach the production server.

### Setup

```bash
export CMDB_URL=https://na1lnptcmdb-01.corp.pvt
export CMDB_API_KEY=<your api key from Admin → Users → Edit>
```

Add these to your `~/.bashrc` or `~/.bash_profile` for persistence.

You can also pass them inline:
```bash
python3 ~/cmdb_cli.py --url https://na1lnptcmdb-01.corp.pvt --key <api_key> nodes
```

### Output Formats

The `-o` / `--output` flag controls output format and must appear **before** the subcommand:

```bash
python3 ~/cmdb_cli.py nodes                    # names only (default)
python3 ~/cmdb_cli.py -o json nodes            # JSON
python3 ~/cmdb_cli.py -o csv  nodes > out.csv  # CSV
```

---

### Querying Nodes

By default, `nodes` prints one node name per line plus a count to stderr. Use `--all-fields` to see the full table with hostname, power state, OS, owner, IP, etc.

#### List nodes (names only)
```bash
python3 ~/cmdb_cli.py nodes
```

#### List nodes with full details
```bash
python3 ~/cmdb_cli.py nodes --all-fields
```

#### Search by name / hostname / IP
```bash
python3 ~/cmdb_cli.py nodes --q web01
python3 ~/cmdb_cli.py nodes --q 10.6.32
```

#### Filter by OS category
```bash
python3 ~/cmdb_cli.py nodes --os linux
python3 ~/cmdb_cli.py nodes --os windows
python3 ~/cmdb_cli.py nodes --os other
```

#### Filter by environment (partial match)
```bash
python3 ~/cmdb_cli.py nodes --env PROD
python3 ~/cmdb_cli.py nodes --env DEV
```

#### Filter by tier (partial match)
```bash
python3 ~/cmdb_cli.py nodes --tier Production
python3 ~/cmdb_cli.py nodes --tier prod      # matches "Production", "prod-web", etc.
```

#### Filter by owner (partial match)
```bash
python3 ~/cmdb_cli.py nodes --owner "Josh Marceaux"
python3 ~/cmdb_cli.py nodes --owner kuriger   # partial match — no need for full name
python3 ~/cmdb_cli.py nodes --owner michael   # matches any owner with "michael" in the name
```

#### Filter by power state
```bash
python3 ~/cmdb_cli.py nodes --power poweredOn
python3 ~/cmdb_cli.py nodes --power poweredOff
```

#### Filter by tag
```bash
python3 ~/cmdb_cli.py nodes --tag kubernetes
```

#### Filter by group
```bash
python3 ~/cmdb_cli.py nodes --group hadoop
```

#### Combine filters
```bash
python3 ~/cmdb_cli.py nodes --os linux --env PROD --power poweredOn
python3 ~/cmdb_cli.py nodes --owner sherpa --os linux --all-fields
```

#### Include inactive nodes
```bash
python3 ~/cmdb_cli.py nodes --all
```

#### Limit results
```bash
python3 ~/cmdb_cli.py nodes --os linux --limit 50
```

#### Export all Linux PROD nodes to CSV
```bash
python3 ~/cmdb_cli.py -o csv nodes --os linux --env PROD > linux_prod.csv
```

#### Show full detail for a single node (by ID)
```bash
python3 ~/cmdb_cli.py node 4965
python3 ~/cmdb_cli.py -o json node 4965
```

---

### Group Management

#### List all groups (names only — good for scripting)
```bash
python3 ~/cmdb_cli.py groups
```

#### List with full details (id, flags, description, node count)
```bash
python3 ~/cmdb_cli.py groups --all-fields
```

#### Filter by Ansible or Nagios flag
```bash
python3 ~/cmdb_cli.py groups --ansible
python3 ~/cmdb_cli.py groups --nagios
python3 ~/cmdb_cli.py groups --ansible --nagios   # groups with both flags
```

#### Create a group
```bash
# Custom group (no automation flags)
python3 ~/cmdb_cli.py creategroup decommission-candidates --desc "To be decommissioned Q3"

# Ansible inventory group only
python3 ~/cmdb_cli.py creategroup web-prod --ansible --desc "Production web tier"

# Nagios hostgroup only
python3 ~/cmdb_cli.py creategroup db-servers --nagios --desc "All database servers"

# Both Ansible and Nagios
python3 ~/cmdb_cli.py creategroup hadoop --ansible --nagios --desc "Hadoop cluster nodes"
```

#### Add nodes to a group

Supports exact names, comma-separated lists, and `*` / `?` wildcards:

```bash
# Add by exact name
python3 ~/cmdb_cli.py addtogroup --name namenode01 --group hadoop

# Add by wildcard
python3 ~/cmdb_cli.py addtogroup --name "hadoop*" --group hadoop

# Add multiple patterns at once
python3 ~/cmdb_cli.py addtogroup --name "namenode*,datanode*,jobtracker01" --group hadoop

# Add to multiple groups simultaneously
python3 ~/cmdb_cli.py addtogroup --name "web*" --group web-prod,ansible-web
```

#### Remove nodes from a group
```bash
python3 ~/cmdb_cli.py removefromgroup --name "hadoop-old*" --group hadoop
python3 ~/cmdb_cli.py removefromgroup --name "node01,node02" --group hadoop,web-prod
```

#### Show all nodes in a group
```bash
python3 ~/cmdb_cli.py groupshow hadoop
python3 ~/cmdb_cli.py -o json groupshow hadoop
python3 ~/cmdb_cli.py -o csv  groupshow hadoop > hadoop_nodes.csv
```

#### Delete a group
```bash
# With confirmation prompt
python3 ~/cmdb_cli.py deletegroup --group old-group

# Delete multiple groups
python3 ~/cmdb_cli.py deletegroup --group "group1,group2,group3"

# Skip confirmation (for scripts)
python3 ~/cmdb_cli.py deletegroup --group old-group --force
```

---

### Lookup Commands

These return tables of the reference objects:

```bash
python3 ~/cmdb_cli.py os            # all operating systems with node counts
python3 ~/cmdb_cli.py environments  # all environments
python3 ~/cmdb_cli.py tiers         # all tiers
python3 ~/cmdb_cli.py owners        # all owners with node counts
python3 ~/cmdb_cli.py tags          # all tags
python3 ~/cmdb_cli.py ips           # all IP addresses
python3 ~/cmdb_cli.py scan-runs     # recent sync history
```

All support `-o json` and `-o csv`.

---

### Scripting Examples

**Generate a flat list of all Ansible group names:**
```bash
python3 ~/cmdb_cli.py groups --ansible
```

**Get all powered-on Linux nodes in JSON for an Ansible dynamic inventory script:**
```bash
python3 ~/cmdb_cli.py -o json nodes --os linux --power poweredOn
```

**Find all nodes belonging to a specific owner and export:**
```bash
python3 ~/cmdb_cli.py -o csv nodes --owner kuriger > nodes.csv
```

**Bulk-add nodes to a group from a file:**
```bash
# nodes.txt contains one name/pattern per line
cat nodes.txt | tr '\n' ',' | xargs -I{} python3 ~/cmdb_cli.py addtogroup --name {} --group my-group --force
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
└── vcenter/                        # vCenter sync scripts
    ├── vcenter_inventory.py        # Full vCenter export → CSV
    ├── cmdb_import.py              # CSV → MySQL import
    ├── cmdb_sync_fast.py           # Incremental event-based sync
    ├── cmdb_cron.sh                # Cron wrapper (fast + full modes)
    ├── na1                         # govc env file for PHX1 vCenter
    ├── ev3                         # govc env file for ev3 vCenter
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
*/30 * * * *  /home/mk7193/vcenter/cmdb_cron.sh fast >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
0    2 * * *  /home/mk7193/vcenter/cmdb_cron.sh full >> /home/mk7193/vcenter/logs/cmdb_sync.log 2>&1
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
~/vcenter/cmdb_cron.sh full                              # run a full sync now
~/vcenter/cmdb_cron.sh fast                              # run a fast incremental sync now
python3 ~/vcenter/cmdb_sync_fast.py --dry-run            # fast sync without DB writes
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
