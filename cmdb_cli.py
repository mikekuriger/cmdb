#!/usr/bin/env python3
"""
cmdb — Infrastructure CMDB command-line tool.
v 1.2

Setup:
  export CMDB_URL=https://na1lnptcmdb-01.corp.pvt
  export CMDB_API_KEY=<key from Admin → Users → Edit>
  export CMDB_INSECURE=1        # skip SSL verify for self-signed cert

Examples:
  cmdb --name web01
  cmdb --get os=linux
  cmdb --get env=PROD --get power=poweredOn --allfields
  cmdb --name web01 --fields hostname,power_state,groups
  cmdb --exactget name=web01.corp.pvt --fields name,hostname,groups
  cmdb --get owner=kuriger -o csv > nodes.csv

  cmdb --name web01 --set owner=kuriger --yes
  cmdb --get env=DEV --set tier=Development --yes
  cmdb --name web01 --delete --yes
  cmdb --name "hadoop*" --addtonodegroup hadoop-prod --yes
  cmdb --name "hadoop*" --removefromnodegroup old-group --yes

  cmdb --objecttype groups
  cmdb --objecttype groups --get name=hadoop
  cmdb --objecttype groups --exactget name=hadoop --shownodes
  cmdb --objecttype groups --exactget name=old-group --delete --yes
  cmdb --createnodegroup hadoop --ansible --nagios --desc "Hadoop cluster"

  cmdb --lookup owners
  cmdb --lookup os
  cmdb --lookup scan-runs
"""

import argparse, json, os, sys, urllib.request, urllib.parse, urllib.error, ssl

VERSION = '1.2'
from datetime import datetime

BASE_URL = os.environ.get('CMDB_URL', 'http://localhost:5000')
API_KEY  = os.environ.get('CMDB_API_KEY', '')
INSECURE = os.environ.get('CMDB_INSECURE', '1').lower() not in ('0', 'false', 'no')

# ---------------------------------------------------------------------------
# Field maps
# ---------------------------------------------------------------------------

# --get / --exactget field → API query param ('q' = general search, searches name+hostname+IP+OS)
_GET_FIELD_API = {
    'name':        'q',
    'hostname':    'q',
    'power':       'power',       'power_state':  'power',
    'os':          'guest_os',    'os_category':  'os_category',
    'guest_os':    'guest_os',    'full_os':       'guest_os',
    'env':         'env',         'environment':  'env',
    'tier':        'tier',
    'owner':       'owner',
    'tag':         'tag',         'tags':         'tag',
    'group':       'group',       'node_group':   'group',  'node_groups': 'group',
    'datacenter':  'datacenter',
    'vcenter':     'vcenter',
    'status':      'status',
    'deployment':  'deployment',
}

# --set field → API field (prefix __ = needs lookup by name first)
_SET_FIELD_API = {
    'name':        'name',
    'hostname':    'hostname',
    'purpose':     'purpose',
    'landscape':   'landscape',
    'app_name':    'app_name',
    'description': 'description',
    'deployment':  'deployment',
    'cmdb_uuid':   'cmdb_uuid',
    'owner':       '__owner',
    'environment': '__environment',  'env': '__environment',
    'tier':        '__tier',
    'status':      '__status',
}

# --fields alias → node dict key (or special sentinel starting with _)
_FIELD_DISPLAY = {
    'name':         'name',
    'hostname':     'hostname',
    'power':        'power_state',  'power_state':  'power_state',
    'os':           'guest_os',     'guest_os':      'guest_os',
    'os_category':  'os_category',
    'env':          'environment',  'environment':   'environment',
    'tier':         'tier',
    'owner':        'owner',
    'ip':           'primary_ip',   'primary_ip':    'primary_ip',
    'datacenter':   'datacenter',
    'groups':       '_groups',      'node_groups':   '_groups',
    'tags':         '_tags',
    'template':     'is_template',  'is_template':   'is_template',
    'active':       'active',
    'cpus':         'cpus',
    'memory_gb':    'memory_gb',
    'purpose':      'purpose',
    'app_name':     'app_name',
    'description':  'description',
    'deployment':   'deployment',
    'cmdb_uuid':    'cmdb_uuid',
    'first_seen':   'first_seen',
    'last_seen':    'last_seen',
    'status':       'status',
    'id':           'id',
    'vcenter':      'vcenter_label',
}

_ALL_FIELDS = ['name', 'hostname', 'power_state', 'os_category', 'environment',
               'tier', 'owner', 'primary_ip', 'datacenter', 'is_template',
               'active', 'cpus', 'memory_gb', 'last_seen']

_LOOKUP_TYPES = {
    'os':           ('os',           ['full_name', 'category', 'family', 'node_count']),
    'environments': ('environments', ['name', 'node_count']),
    'envs':         ('environments', ['name', 'node_count']),
    'tiers':        ('tiers',        ['name', 'node_count']),
    'owners':       ('owners',       ['name', 'email', 'node_count']),
    'tags':         ('tags',         ['name', 'category', 'node_count']),
    'ips':          ('ips',          ['ip', 'node_name', 'is_primary', 'source']),
    'scan-runs':    ('scan-runs',    ['id', 'source', 'started_at', 'finished_at', 'duration_secs']),
    'vcenters':     ('vcenters',     ['label', 'url', 'datacenter_count']),
}


def _strip_bracket(f):
    """'node_groups[name]' → 'node_groups'"""
    return f.split('[')[0].strip()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _ssl_ctx():
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _http(url, method='GET', body=None):
    headers = {'Authorization': f'Bearer {API_KEY}', 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code}: {e.read().decode()}', file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f'Connection error: {e.reason}', file=sys.stderr)
        sys.exit(1)


def _url(path):
    return BASE_URL.rstrip('/') + '/api/v1/' + path


def api_get(path, params=None):
    url = _url(path)
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ''}
        if clean:
            url += '?' + urllib.parse.urlencode(clean)
    return _http(url)


def api_post(path, body):   return _http(_url(path), 'POST', json.dumps(body).encode())
def api_put(path, body):    return _http(_url(path), 'PUT',  json.dumps(body).encode())
def api_delete(path):       return _http(_url(path), 'DELETE')


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_table(rows, keys):
    if not rows:
        print('No results.')
        return
    widths = {k: max(len(str(k)), max((len(str(r.get(k) or '')) for r in rows), default=0))
              for k in keys}
    widths = {k: min(v, 80) for k, v in widths.items()}
    sep = '  '
    print(sep.join(str(k).ljust(widths[k]) for k in keys))
    print(sep.join('─' * widths[k] for k in keys))
    for r in rows:
        print(sep.join(str(r.get(k) or '').ljust(widths[k])[:widths[k]] for k in keys))


def _fmt_csv(rows, keys):
    print(','.join(keys))
    for r in rows:
        print(','.join(f'"{str(r.get(k) or "").replace(chr(34), chr(39))}"' for k in keys))


def _parse_kv(values):
    """['field=value', ...] → [(field, value), ...]  Handles 'field=val=with=equals' correctly."""
    result = []
    for v in (values or []):
        if '=' not in v:
            sys.exit(f'Error: expected field=value, got: {v!r}')
        k, _, val = v.partition('=')
        result.append((_strip_bracket(k.strip()), val.strip()))
    return result


def _confirm(prompt):
    try:
        return input(prompt + ' [y/N] ').strip().lower() == 'y'
    except (EOFError, KeyboardInterrupt):
        print(); return False


def _strip_wildcards(v):
    """'web*' → 'web'  (the API does substring search; wildcards are redundant)"""
    return v.rstrip('*').rstrip('?').strip()


# ---------------------------------------------------------------------------
# Node fetch + display
# ---------------------------------------------------------------------------

def fetch_nodes(get_pairs, exact_pairs, all_nodes=False, limit=5000):
    # Expand comma-separated name/hostname values into per-value fetches
    multi, base_get, base_exact = [], [], []
    for field, value in (get_pairs or []):
        if "," in value:
            multi.extend(("get", field, v.strip()) for v in value.split(",") if v.strip())
        else:
            base_get.append((field, value))
    for field, value in (exact_pairs or []):
        if "," in value:
            multi.extend(("exact", field, v.strip()) for v in value.split(",") if v.strip())
        else:
            base_exact.append((field, value))
    if multi:
        seen, rows = set(), []
        for kind, field, v in multi:
            for r in fetch_nodes(
                base_get + [(field, v)] if kind == "get" else base_get,
                base_exact + [(field, v)] if kind == "exact" else base_exact,
                all_nodes, limit,
            ):
                if r.get("id") not in seen:
                    seen.add(r["id"]); rows.append(r)
        return rows

    params = {"limit": limit, "active": "all" if all_nodes else "1"}
    # exact-match fields that need post-filtering (API only does substring on q=)
    post_exact = {}

    for field, value in get_pairs:
        api_key = _GET_FIELD_API.get(field)
        if api_key:
            params[api_key] = _strip_wildcards(value) if api_key == 'q' else value
        else:
            print(f'Warning: unknown filter field {field!r} — ignored', file=sys.stderr)

    for field, value in exact_pairs:
        api_key = _GET_FIELD_API.get(field)
        if api_key == 'q':
            params['q'] = _strip_wildcards(value)
            post_exact[field] = value.lower()
        elif api_key:
            params[api_key] = value
        else:
            print(f'Warning: unknown exactget field {field!r} — ignored', file=sys.stderr)

    resp = api_get('nodes', params)
    rows = resp.get('data', resp) if isinstance(resp, dict) else list(resp)

    for field, value in post_exact.items():
        rows = [r for r in rows if (r.get(field) or '').lower() == value]

    return rows


def _enrich_for_display(rows, display_keys):
    """Add synthesized _groups and _tags keys if needed."""
    need_groups = '_groups' in display_keys
    need_tags   = '_tags'   in display_keys

    if not need_groups and not need_tags:
        return

    # groups not in list endpoint — fetch individually (warn if many)
    if need_groups:
        if len(rows) > 50:
            print(f'Note: fetching groups for {len(rows)} nodes — this may take a moment',
                  file=sys.stderr)
        for r in rows:
            detail = api_get(f'nodes/{r["id"]}')
            r['_groups'] = ', '.join(g['name'] for g in (detail.get('groups') or []))
    if need_tags:
        for r in rows:
            raw = r.get('tags') or ''
            r['_tags'] = raw.replace('|', ', ')


def display_nodes(rows, display_keys, fmt, labels=None):
    """
    display_keys : list of row-dict keys to display, or None for name-only
    labels       : original field names as typed (same length as display_keys);
                   when set, uses nv record format (name: / field: value);
                   when None, uses flat table (used by --allfields)
    """
    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print('No results.')
        return

    if display_keys is None:
        # Default: one name per line
        for r in rows:
            print(r.get('name', ''))
        print(f'{len(rows)} node(s)', file=sys.stderr)
        return

    _enrich_for_display(rows, display_keys)

    if fmt == 'csv':
        _fmt_csv(rows, display_keys)
        print(f'{len(rows)} node(s)', file=sys.stderr)
        return

    if labels:
        # nv-style record format: one block per node
        pairs = list(zip(labels, display_keys))
        for r in rows:
            print(f'{r.get("name", "(unknown)")}:')
            for label, key in pairs:
                val = r.get(key)
                print(f'  {label}: {val if val is not None else ""}')
            print()
    else:
        # Flat table — used for --allfields
        _fmt_table(rows, display_keys)
    print(f'{len(rows)} node(s)', file=sys.stderr)


# ---------------------------------------------------------------------------
# Node actions
# ---------------------------------------------------------------------------

def _resolve_id(endpoint, value, name_field='name'):
    """Fetch all records from endpoint, return id of the one whose name_field matches value."""
    resp = api_get(endpoint)
    rows = resp.get('data', [])
    exact = [r for r in rows if (r.get(name_field) or '').lower() == value.lower()]
    if not exact:
        partial = [r for r in rows if value.lower() in (r.get(name_field) or '').lower()]
        if len(partial) == 1:
            return partial[0]['id'], partial[0][name_field]
        candidates = ', '.join(r[name_field] for r in partial[:5])
        hint = f'  Candidates: {candidates}' if partial else '  No matches found.'
        sys.exit(f'Error: no exact match for {name_field}={value!r} in {endpoint}.{hint}')
    return exact[0]['id'], exact[0][name_field]


def do_set_nodes(rows, set_pairs, yes, dry_run, fmt):
    if not rows:
        print('No nodes matched.'); return

    if not yes and not dry_run:
        names = ', '.join(r['name'] for r in rows[:5])
        if len(rows) > 5: names += f' … ({len(rows)} total)'
        if not _confirm(f'Update {len(rows)} node(s) ({names})?'):
            print('Aborted.'); return

    # Resolve set values → actual API payload
    updates = {}
    for field, value in set_pairs:
        api_field = _SET_FIELD_API.get(field)
        if api_field is None:
            sys.exit(f'Error: field {field!r} is not settable. '
                     f'Settable fields: {", ".join(_SET_FIELD_API)}')
        if api_field == '__owner':
            oid, oname = _resolve_id('owners', value)
            updates['owner_id'] = oid
            if not dry_run: print(f'  resolved owner: {value!r} → {oname} (id={oid})')
        elif api_field == '__environment':
            eid, ename = _resolve_id('environments', value)
            updates['environment_id'] = eid
            if not dry_run: print(f'  resolved environment: {value!r} → {ename} (id={eid})')
        elif api_field == '__tier':
            tid, tname = _resolve_id('tiers', value)
            updates['tier_id'] = tid
            if not dry_run: print(f'  resolved tier: {value!r} → {tname} (id={tid})')
        elif api_field == '__status':
            sid, sname = _resolve_id('statuses', value)
            updates['status_id'] = sid
            if not dry_run: print(f'  resolved status: {value!r} → {sname} (id={sid})')
        else:
            updates[api_field] = value

    if dry_run:
        for r in rows:
            print(f'[dry-run] Would update {r["name"]}: {updates}')
        return

    for r in rows:
        api_put(f'nodes/{r["id"]}', updates)
        print(f'  updated: {r["name"]}')

    if fmt == 'json':
        print(json.dumps({'updated': len(rows), 'fields': updates}))


def do_delete_nodes(rows, yes, dry_run):
    if not rows:
        print('No nodes matched.'); return

    if not yes and not dry_run:
        names = ', '.join(r['name'] for r in rows[:5])
        if len(rows) > 5: names += f' … ({len(rows)} total)'
        if not _confirm(f'Permanently delete {len(rows)} node(s) ({names})?'):
            print('Aborted.'); return

    if dry_run:
        for r in rows:
            print(f'[dry-run] Would delete: {r["name"]} (id={r["id"]})')
        return

    for r in rows:
        api_delete(f'nodes/{r["id"]}')
        print(f'  deleted: {r["name"]} (id={r["id"]})')


def _group_ids_from_names(group_spec):
    """'g1,g2' or ['g1,g2', 'g3'] → [(gid, gname), ...]"""
    raw = ','.join(group_spec) if isinstance(group_spec, list) else group_spec
    names = [g.strip() for g in raw.split(',') if g.strip()]
    result = []
    for name in names:
        resp = api_get(f'groups/by-name/{urllib.parse.quote(name, safe="")}')
        if 'error' in resp:
            print(f'  group not found: {name!r}', file=sys.stderr)
            continue
        result.append((resp['id'], resp['name']))
    return result


def do_add_to_groups(rows, group_spec, yes, dry_run):
    if not rows:
        print('No nodes matched.'); return

    groups = _group_ids_from_names(group_spec)
    if not groups: return

    if not yes and not dry_run:
        node_names = ', '.join(r['name'] for r in rows[:5])
        if len(rows) > 5: node_names += f' … ({len(rows)} total)'
        gnames = ', '.join(n for _, n in groups)
        if not _confirm(f'Add {len(rows)} node(s) to [{gnames}]?'):
            print('Aborted.'); return

    node_ids = [r['id'] for r in rows]
    for gid, gname in groups:
        if dry_run:
            print(f'[dry-run] Would add {len(rows)} node(s) to group: {gname}')
            continue
        result = _http(_url(f'groups/{gid}/nodes'), 'POST',
                       json.dumps({'ids': node_ids}).encode())
        print(f'  {gname}: +{result.get("added", 0)} node(s)')


def do_remove_from_groups(rows, group_spec, yes, dry_run):
    if not rows:
        print('No nodes matched.'); return

    groups = _group_ids_from_names(group_spec)
    if not groups: return

    if not yes and not dry_run:
        node_names = ', '.join(r['name'] for r in rows[:5])
        if len(rows) > 5: node_names += f' … ({len(rows)} total)'
        gnames = ', '.join(n for _, n in groups)
        if not _confirm(f'Remove {len(rows)} node(s) from [{gnames}]?'):
            print('Aborted.'); return

    node_ids = [r['id'] for r in rows]
    for gid, gname in groups:
        if dry_run:
            print(f'[dry-run] Would remove {len(rows)} node(s) from group: {gname}')
            continue
        result = _http(_url(f'groups/{gid}/nodes'), 'DELETE',
                       json.dumps({'ids': node_ids}).encode())
        print(f'  {gname}: -{result.get("removed", 0)} node(s)')


# ---------------------------------------------------------------------------
# Groups mode
# ---------------------------------------------------------------------------

def fetch_groups(get_pairs, exact_pairs):
    params = {}
    post_exact = {}
    for field, value in get_pairs:
        if field == 'name':
            params['q'] = _strip_wildcards(value)
        elif field in ('ansible', 'is_ansible'):
            params['ansible'] = 1
        elif field in ('nagios', 'is_nagios'):
            params['nagios'] = 1
    for field, value in exact_pairs:
        if field == 'name':
            params['q'] = _strip_wildcards(value)
            post_exact['name'] = value.lower()
    rows = list(api_get('groups', params).get('data', []))
    for field, value in post_exact.items():
        rows = [r for r in rows if (r.get(field) or '').lower() == value]
    return rows


def display_groups(rows, fmt):
    if not rows:
        print('No results.'); return
    keys = ['name', 'is_ansible', 'is_nagios', 'node_count', 'description']
    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == 'csv':
        _fmt_csv(rows, keys)
    else:
        _fmt_table(rows, keys)
    print(f'{len(rows)} group(s)', file=sys.stderr)


def do_create_groups(group_spec, ansible, nagios, desc, dry_run):
    names = [n.strip() for n in group_spec.split(',') if n.strip()]
    for name in names:
        if dry_run:
            flags = ','.join(filter(None,
                ['ansible' if ansible else '', 'nagios' if nagios else '']))
            print(f'[dry-run] Would create group: {name} [{flags}]')
            continue
        body = {'name': name, 'is_ansible': 1 if ansible else 0,
                'is_nagios': 1 if nagios else 0, 'description': desc or ''}
        resp = api_post('groups', body)
        print(f'  created: {name} (id={resp.get("id")})')


def do_delete_groups(rows, yes, dry_run):
    if not rows:
        print('No groups matched.'); return

    if not yes and not dry_run:
        names = ', '.join(r['name'] for r in rows[:5])
        if len(rows) > 5: names += f' … ({len(rows)} total)'
        if not _confirm(f'Delete {len(rows)} group(s) ({names})? Nodes will NOT be deleted.'):
            print('Aborted.'); return

    if dry_run:
        for r in rows: print(f'[dry-run] Would delete group: {r["name"]}')
        return

    for r in rows:
        api_delete(f'groups/{r["id"]}')
        print(f'  deleted: {r["name"]}')


def do_show_group_nodes(groups, display_keys, fmt):
    for g in groups:
        resp = api_get(f'groups/{g["id"]}/nodes')
        nodes = resp.get('data', [])
        if fmt == 'json':
            print(json.dumps({'group': g['name'], 'nodes': nodes}, indent=2, default=str))
        else:
            print(f'Group: {g["name"]}  ({len(nodes)} nodes)')
            display_nodes(nodes, display_keys or _ALL_FIELDS, fmt)


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

def do_lookup(ltype, fmt):
    ltype = ltype.rstrip('s') + ('s' if not ltype.endswith('s') else '')  # gentle normalise
    # exact match first
    if ltype not in _LOOKUP_TYPES:
        # try without trailing s normalisation
        ltype_raw = ltype
        for k in _LOOKUP_TYPES:
            if k.startswith(ltype_raw) or ltype_raw.startswith(k.rstrip('s')):
                ltype = k; break
    if ltype not in _LOOKUP_TYPES:
        sys.exit(f'Unknown lookup type: {ltype!r}. '
                 f'Available: {", ".join(_LOOKUP_TYPES)}')
    endpoint, cols = _LOOKUP_TYPES[ltype]
    resp = api_get(endpoint)
    rows = list(resp.get('data', resp) if isinstance(resp, dict) else resp)
    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == 'csv':
        _fmt_csv(rows, cols)
    else:
        _fmt_table(rows, cols)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog='cmdb',
        description=f'Infrastructure CMDB command-line tool  v{VERSION}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
QUERY
  --name VALUE              Shortcut for --get name=VALUE
  --get FIELD=VALUE         Partial/substring match filter. Repeatable.
  --exactget FIELD=VALUE    Exact match filter. Repeatable.
  --all                     Include inactive nodes

  Queryable fields: name, hostname, power/power_state, os/os_category,
    guest_os/full_os (e.g. "centos 6", "oracle linux 8", "ubuntu"),
    env/environment, tier, owner, tag, group/node_groups, datacenter, vcenter

DISPLAY
  --fields F1,F2            Show only these columns
  --allfields               Show full table (name, hostname, power, OS, env, owner, IP, …)
  -o table|json|csv         Output format (default: table)

  Displayable fields: name, hostname, power_state, os, os_category, env,
    tier, owner, ip, datacenter, groups/node_groups, tags, template, active,
    cpus, memory_gb, purpose, app_name, description, deployment, cmdb_uuid,
    first_seen, last_seen, id

SET
  --set FIELD=VALUE         Update field on matched nodes. Repeatable.
  Settable fields: name, hostname, purpose, landscape, app_name, description,
    deployment, cmdb_uuid, owner, environment/env, tier

EXAMPLES
  cmdb --name web01
  cmdb --get os=linux --get env=PROD --allfields
  cmdb --exactget name=web01.corp.pvt --fields hostname,groups
  cmdb --get owner=kuriger -o csv > nodes.csv

  cmdb --name web01 --set owner=kuriger --yes
  cmdb --get env=DEV --set tier=Development --yes
  cmdb --name web01 --delete --yes
  cmdb --name "hadoop*" --addtonodegroup prod-hadoop --yes

  cmdb --objecttype groups
  cmdb --objecttype groups --get name=hadoop --shownodes
  cmdb --objecttype groups --exactget name=old-group --delete --yes
  cmdb --createnodegroup hadoop --ansible --nagios --desc "Hadoop cluster"

  cmdb --lookup owners
  cmdb --lookup scan-runs
  cmdb --lookup os
""",
    )

    # Connection
    p.add_argument('--url',      metavar='URL',
                   help=f'CMDB base URL (default: env CMDB_URL or {BASE_URL})')
    p.add_argument('--key',      metavar='KEY',
                   help='API key (default: env CMDB_API_KEY)')
    p.add_argument('--insecure', '-k', action='store_true',
                   help='Skip SSL cert verification (or set CMDB_INSECURE=1)')

    # Object type
    p.add_argument('--objecttype', '--ot', default='nodes', metavar='TYPE',
                   help='Object type: nodes (default) | groups')

    # Query
    p.add_argument('--get',      action='append', metavar='FIELD=VALUE',
                   help='Partial match filter. Repeatable.')
    p.add_argument('--exactget', action='append', metavar='FIELD=VALUE',
                   help='Exact match filter. Repeatable.')
    p.add_argument('--name',     action='append', metavar='VALUE',
                   help='Shortcut for --get name=VALUE. Repeatable.')
    p.add_argument('--all',      action='store_true',
                   help='Include inactive nodes')

    # Display
    p.add_argument('--fields',    metavar='F1,F2',
                   help='Comma-separated fields to display.')
    p.add_argument('--allfields', action='store_true',
                   help='Show all fields (wide table)')
    p.add_argument('-o', '--output', choices=['table', 'json', 'csv'], default='table',
                   help='Output format (default: table)')
    p.add_argument('--shownodes', action='store_true',
                   help='With --objecttype groups: show member nodes')

    # Actions
    p.add_argument('--set',      action='append', metavar='FIELD=VALUE',
                   help='Set field on matched nodes. Repeatable.')
    p.add_argument('--delete',   action='store_true',
                   help='Delete matched objects')
    p.add_argument('--addtonodegroup', '--addnodegroup', metavar='GROUP[,…]',
                   help='Add matched nodes to group(s). Comma-separated.')
    p.add_argument('--removefromnodegroup', '--removenodegroup', metavar='GROUP[,…]',
                   help='Remove matched nodes from group(s). Comma-separated.')

    # Group creation
    p.add_argument('--createnodegroup', metavar='NAME[,…]',
                   help='Create group(s). Comma-separated.')
    p.add_argument('--ansible',  action='store_true',
                   help='With --createnodegroup: mark as Ansible inventory group')
    p.add_argument('--nagios',   action='store_true',
                   help='With --createnodegroup: mark as Nagios hostgroup')
    p.add_argument('--desc',     metavar='TEXT', default='',
                   help='With --createnodegroup: description')

    # Lookup / misc
    p.add_argument('--lookup',    metavar='TYPE',
                   help='List reference data: os, owners, environments, tiers, tags, ips, scan-runs, vcenters')
    p.add_argument('--scan-runs', action='store_true', dest='scan_runs',
                   help='Shortcut for --lookup scan-runs')

    # Behaviour
    p.add_argument('--yes', '-y', action='store_true',
                   help='Skip confirmation prompts')
    p.add_argument('--dry-run',  action='store_true', dest='dry_run',
                   help='Show what would change without making changes')
    p.add_argument('--limit',    type=int, default=5000,
                   help='Max nodes to return (default: 5000)')
    p.add_argument('--version', '-v', action='version',
                   version=f'cmdb {VERSION}')

    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global BASE_URL, API_KEY

    p = build_parser()
    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(0)
    args = p.parse_args()

    if args.url:      BASE_URL = args.url
    if args.key:      API_KEY  = args.key
    if args.insecure: globals().__setitem__('INSECURE', True)

    if not API_KEY:
        sys.exit('Error: CMDB_API_KEY not set. Use --key or export CMDB_API_KEY=<token>')

    fmt = args.output

    # ── Lookup shortcuts ─────────────────────────────────────────────────
    if args.scan_runs:
        do_lookup('scan-runs', fmt); return
    if args.lookup:
        do_lookup(args.lookup, fmt); return

    # ── Create group ──────────────────────────────────────────────────────
    if args.createnodegroup:
        do_create_groups(args.createnodegroup, args.ansible, args.nagios,
                         args.desc, args.dry_run)
        return

    # ── Build filter pairs ────────────────────────────────────────────────
    get_pairs   = _parse_kv(args.get or [])
    exact_pairs = _parse_kv(args.exactget or [])
    set_pairs   = _parse_kv(args.set or [])

    if args.name:
        get_pairs += [('name', n) for n in args.name]

    # ── Resolve display fields ────────────────────────────────────────────
    display_labels = None  # when set, triggers nv record format
    if args.allfields:
        display_keys = list(_ALL_FIELDS)
        # display_labels stays None → flat table
    elif args.fields:
        raw = [_strip_bracket(f.strip()) for f in args.fields.split(',')]
        display_keys  = [_FIELD_DISPLAY.get(f, f) for f in raw]
        display_labels = raw  # preserve original names as block labels
    else:
        display_keys = None  # name-only

    # ── Groups mode ───────────────────────────────────────────────────────
    if args.objecttype.rstrip('s') in ('group', 'node_group', 'nodegroup'):
        rows = fetch_groups(get_pairs, exact_pairs)
        if args.delete:
            do_delete_groups(rows, args.yes, args.dry_run); return
        if args.shownodes:
            do_show_group_nodes(rows, display_keys, fmt); return
        display_groups(rows, fmt)
        return

    # ── Nodes mode ────────────────────────────────────────────────────────
    # If no query at all and no action, default to --allfields for usability
    has_query  = bool(get_pairs or exact_pairs)
    has_action = bool(set_pairs or args.delete or
                      args.addtonodegroup or args.removefromnodegroup)

    if not has_query and not has_action and display_keys is None:
        display_keys = list(_ALL_FIELDS)

    rows = fetch_nodes(get_pairs, exact_pairs, all_nodes=args.all, limit=args.limit)

    if args.delete:
        do_delete_nodes(rows, args.yes, args.dry_run); return

    if args.addtonodegroup:
        do_add_to_groups(rows, args.addtonodegroup, args.yes, args.dry_run); return

    if args.removefromnodegroup:
        do_remove_from_groups(rows, args.removefromnodegroup, args.yes, args.dry_run); return

    if set_pairs:
        do_set_nodes(rows, set_pairs, args.yes, args.dry_run, fmt)
        # fall through to display if --fields was also given
        if not args.fields and not args.allfields:
            return

    display_nodes(rows, display_keys, fmt, labels=display_labels)


if __name__ == '__main__':
    main()
