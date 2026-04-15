#!/usr/bin/env python3
"""
cmdb — Command-line CMDB query tool.

Setup:
  export CMDB_URL=https://na1lnptcmdb-01.corp.pvt
  export CMDB_API_KEY=<key from admin UI>
  export CMDB_INSECURE=1        # skip SSL verify for self-signed cert

Examples:
  cmdb nodes
  cmdb nodes --os linux
  cmdb nodes --env PROD --power poweredOn
  cmdb nodes --owner "TeamName"
  cmdb nodes --tag kubernetes --os linux
  cmdb nodes --group ansible-web
  cmdb nodes --q web01 -o json
  cmdb node 4312
  cmdb os
  cmdb owners
  cmdb tags
  cmdb groups
  cmdb scan-runs
  cmdb nodes --os windows -o csv > windows.csv
"""

import argparse, json, os, sys, urllib.request, urllib.parse, urllib.error, ssl
from datetime import datetime

BASE_URL = os.environ.get('CMDB_URL', 'http://localhost:5000')
API_KEY  = os.environ.get('CMDB_API_KEY', '')
INSECURE = os.environ.get('CMDB_INSECURE', '1').lower() not in ('0', 'false', 'no')


def _ssl_ctx():
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def api_get(path, params=None):
    url = BASE_URL.rstrip('/') + '/api/v1/' + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ''}
        if clean:
            url += '?' + urllib.parse.urlencode(clean)
    req = urllib.request.Request(
        url, headers={'Authorization': f'Bearer {API_KEY}', 'Accept': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f'HTTP {e.code}: {body}', file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f'Connection error: {e.reason}', file=sys.stderr)
        print(f'Is CMDB_URL correct? ({BASE_URL})', file=sys.stderr)
        sys.exit(1)


def _fmt_table(rows, keys):
    if not rows:
        print('No results.')
        return
    widths = {}
    for k in keys:
        widths[k] = max(len(str(k)), max(len(str(r.get(k) or '')) for r in rows))
        widths[k] = min(widths[k], 60)  # cap column width
    sep = '  '
    header = sep.join(str(k).ljust(widths[k]) for k in keys)
    print(header)
    print(sep.join('─' * widths[k] for k in keys))
    for r in rows:
        print(sep.join(str(r.get(k) or '').ljust(widths[k])[:widths[k]] for k in keys))


def _fmt_csv(rows):
    if not rows:
        return
    keys = list(rows[0].keys())
    print(','.join(keys))
    for r in rows:
        print(','.join(f'"{str(r.get(k) or "").replace(chr(34), chr(39))}"' for k in keys))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_nodes(args, fmt):
    params = {
        'limit':       args.limit or 5000,
        'q':           args.q,
        'power':       args.power,
        'os_category': args.os,
        'env':         args.env,
        'tier':        args.tier,
        'owner':       args.owner,
        'tag':         args.tag,
        'group':       args.group,
        'active':      'all' if args.all else '1',
    }
    resp = api_get('nodes', params)
    rows = resp.get('data', resp)
    total = resp.get('total', len(rows))

    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == 'csv':
        _fmt_csv(rows)
    elif getattr(args, 'all_fields', False):
        _fmt_table(rows, ['name', 'hostname', 'power_state', 'os_category',
                          'environment', 'tier', 'owner', 'primary_ip', 'datacenter'])
        print(f'\n{len(rows)} of {total} nodes', file=sys.stderr)
    else:
        for r in rows:
            print(r['name'])
        print(f'{len(rows)} of {total} nodes', file=sys.stderr)


def cmd_node(args, fmt):
    resp = api_get(f'nodes/{args.id}')
    if fmt == 'json':
        print(json.dumps(resp, indent=2, default=str))
        return
    skip = {'ip_addresses', 'groups', 'attributes'}
    for k, v in resp.items():
        if k not in skip:
            print(f'  {k:<22} {v}')
    if resp.get('ip_addresses'):
        print('\n  IPs:')
        for ip in resp['ip_addresses']:
            flag = ' [primary]' if ip['is_primary'] else ''
            print(f'    {ip["ip"]}{flag}')
    if resp.get('groups'):
        print('\n  Groups:')
        for g in resp['groups']:
            flags = ','.join(filter(None, [
                'ansible' if g.get('is_ansible') else None,
                'nagios'  if g.get('is_nagios')  else None,
            ])) or 'custom'
            print(f'    {g["name"]}  [{flags}]')
    if resp.get('attributes'):
        print('\n  Attributes:')
        for a in resp['attributes']:
            print(f'    {a["name"]:<20} {a["value"]}')


def cmd_list(endpoint, fmt, params=None):
    resp = api_get(endpoint, params)
    rows = resp.get('data', resp)
    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == 'csv':
        _fmt_csv(rows)
    else:
        if rows:
            _fmt_table(rows, list(rows[0].keys()))
        else:
            print('No results.')


# ---------------------------------------------------------------------------
# Group commands
# ---------------------------------------------------------------------------

def _resolve_group_id(name_or_id):
    """Return group id given a name or numeric id string."""
    try:
        return int(name_or_id)
    except ValueError:
        pass
    resp = api_get(f'groups/by-name/{urllib.parse.quote(name_or_id, safe="")}')
    if 'error' in resp:
        print(f'Group not found: {name_or_id}', file=sys.stderr)
        sys.exit(1)
    return resp['id']


def cmd_creategroup(args, fmt):
    body = {
        'name':        args.name,
        'is_ansible':  1 if args.ansible else 0,
        'is_nagios':   1 if args.nagios  else 0,
        'description': args.description or '',
    }
    url  = BASE_URL.rstrip('/') + '/api/v1/groups'
    req  = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code}: {e.read().decode()}', file=sys.stderr); sys.exit(1)
    if fmt == 'json':
        print(json.dumps(resp, indent=2))
    else:
        print(f"Created group: {args.name}  (id={resp.get('id')})")


def _group_membership_request(method, group_id, names=None, ids=None):
    url  = BASE_URL.rstrip('/') + f'/api/v1/groups/{group_id}/nodes'
    body = {}
    if names: body['names'] = names
    if ids:   body['ids']   = ids
    req  = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code}: {e.read().decode()}', file=sys.stderr); sys.exit(1)
    except urllib.error.URLError as e:
        print(f'Connection error: {e.reason}', file=sys.stderr); sys.exit(1)


def cmd_addtogroup(args, fmt):
    """nventory-style: cmdb addtogroup --name "web*,db01" --group "prod-web,ansible"."""
    names    = [n.strip() for s in args.name for n in s.split(',') if n.strip()]
    grp_names = [g.strip() for s in args.group for g in s.split(',') if g.strip()]

    for grp in grp_names:
        gid  = _resolve_group_id(grp)
        resp = _group_membership_request('POST', gid, names=names)
        if fmt == 'json':
            print(json.dumps({'group': grp, **resp}, indent=2))
        else:
            nf = f"  not found: {', '.join(resp['not_found'])}" if resp.get('not_found') else ''
            print(f"  {grp}: +{resp['added']} node(s){nf}")


def cmd_removefromgroup(args, fmt):
    names    = [n.strip() for s in args.name for n in s.split(',') if n.strip()]
    grp_names = [g.strip() for s in args.group for g in s.split(',') if g.strip()]

    for grp in grp_names:
        gid  = _resolve_group_id(grp)
        resp = _group_membership_request('DELETE', gid, names=names)
        if fmt == 'json':
            print(json.dumps({'group': grp, **resp}, indent=2))
        else:
            print(f"  {grp}: -{resp['removed']} node(s)")


def cmd_deletegroup(args):
    grp_names = [g.strip() for s in args.group for g in s.split(',') if g.strip()]
    for grp in grp_names:
        gid = _resolve_group_id(grp)
        if not args.force:
            confirm = input(f'Delete group "{grp}"? Nodes will NOT be deleted. [y/N] ').strip().lower()
            if confirm != 'y':
                print(f'  skipped: {grp}')
                continue
        url = BASE_URL.rstrip('/') + f'/api/v1/groups/{gid}'
        req = urllib.request.Request(
            url, headers={'Authorization': f'Bearer {API_KEY}'}, method='DELETE'
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()):
                print(f'  deleted: {grp}')
        except urllib.error.HTTPError as e:
            print(f'  error deleting {grp}: {e.read().decode()}', file=sys.stderr)


def cmd_groupshow(args, fmt):
    gid  = _resolve_group_id(args.name)
    resp = api_get(f'groups/{gid}/nodes')
    rows = resp.get('data', [])
    if fmt == 'json':
        print(json.dumps(rows, indent=2, default=str))
    elif fmt == 'csv':
        _fmt_csv(rows)
    else:
        print(f'Group: {args.name}  ({len(rows)} nodes)')
        _fmt_table(rows, ['name', 'hostname', 'power_state', 'os_category',
                          'environment', 'owner', 'primary_ip'])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global BASE_URL, API_KEY
    parser = argparse.ArgumentParser(
        prog='cmdb',
        description='Query the CMDB from the command line.',
        epilog='Set CMDB_URL and CMDB_API_KEY environment variables.',
    )
    parser.add_argument('-o', '--output', choices=['table', 'json', 'csv'], default='table',
                        help='Output format (default: table)')
    parser.add_argument('--url', help=f'CMDB base URL (default: {BASE_URL})')
    parser.add_argument('--key', help='API key (default: $CMDB_API_KEY)')
    parser.add_argument('--insecure', '-k', action='store_true',
                        help='Skip SSL certificate verification (or set CMDB_INSECURE=1)')

    sub = parser.add_subparsers(dest='cmd', required=True)

    # nodes
    pn = sub.add_parser('nodes', help='List/filter nodes')
    pn.add_argument('--q',     metavar='SEARCH', help='Search name, hostname, IP, OS')
    pn.add_argument('--power', metavar='STATE',  help='poweredOn | poweredOff | suspended')
    pn.add_argument('--os',    metavar='CAT',    help='linux | windows | other')
    pn.add_argument('--env',   metavar='NAME',   help='Environment name (e.g. PROD)')
    pn.add_argument('--tier',  metavar='NAME',   help='Tier name')
    pn.add_argument('--owner', metavar='NAME',   help='Owner name')
    pn.add_argument('--tag',   metavar='NAME',   help='Tag name')
    pn.add_argument('--group', metavar='NAME',   help='Group name')
    pn.add_argument('--all',       action='store_true', help='Include inactive nodes')
    pn.add_argument('--limit',     type=int,            help='Max results')
    pn.add_argument('--all-fields', action='store_true', dest='all_fields',
                    help='Show full table (hostname, power, OS, owner, IP, etc.)')

    # node
    pd = sub.add_parser('node', help='Show full detail for a single node')
    pd.add_argument('id', type=int, help='Node ID')

    # lookup tables
    for ep, hlp in [
        ('os',           'List operating systems'),
        ('environments', 'List environments'),
        ('tiers',        'List tiers'),
        ('owners',       'List owners'),
        ('tags',         'List tags'),
        ('ips',          'List IP addresses'),
        ('scan-runs',    'List scan runs'),
    ]:
        sub.add_parser(ep, help=hlp)

    pg = sub.add_parser('groups', help='List groups')
    pg.add_argument('--ansible',    action='store_true', help='Only Ansible inventory groups')
    pg.add_argument('--nagios',     action='store_true', help='Only Nagios hostgroups')
    pg.add_argument('--all-fields', action='store_true', dest='all_fields',
                    help='Show all columns (id, flags, description, node count)')

    # group management
    pcg = sub.add_parser('creategroup', help='Create a new group')
    pcg.add_argument('name', help='Group name')
    pcg.add_argument('--ansible', action='store_true', help='Mark as Ansible inventory group')
    pcg.add_argument('--nagios',  action='store_true', help='Mark as Nagios hostgroup')
    pcg.add_argument('--description', '--desc', metavar='TEXT', default='')

    pag = sub.add_parser('addtogroup',
        help='Add nodes to group(s) by name/pattern  [nventory-style]')
    pag.add_argument('--name', '-n', nargs='+', required=True, metavar='PATTERN',
        help='Node name(s) or patterns, supports * wildcard. Comma-separated ok.')
    pag.add_argument('--group', '-g', nargs='+', required=True, metavar='GROUP',
        help='Group name(s). Comma-separated ok.')

    prg = sub.add_parser('removefromgroup', help='Remove nodes from group(s)')
    prg.add_argument('--name', '-n', nargs='+', required=True, metavar='PATTERN')
    prg.add_argument('--group', '-g', nargs='+', required=True, metavar='GROUP')

    pgs = sub.add_parser('groupshow', help='List nodes in a group')
    pgs.add_argument('name', help='Group name or ID')

    pdg = sub.add_parser('deletegroup', help='Delete one or more groups')
    pdg.add_argument('--group', '-g', nargs='+', required=True, metavar='GROUP',
        help='Group name(s) to delete. Comma-separated ok.')
    pdg.add_argument('--force', '-f', action='store_true',
        help='Skip confirmation prompt')

    args = parser.parse_args()

    if args.url:      BASE_URL = args.url
    if args.key:      API_KEY  = args.key
    if args.insecure: globals().__setitem__('INSECURE', True)

    if not API_KEY:
        print('Error: CMDB_API_KEY not set. Use --key or export CMDB_API_KEY=<token>',
              file=sys.stderr)
        sys.exit(1)

    fmt = args.output
    if   args.cmd == 'nodes':           cmd_nodes(args, fmt)
    elif args.cmd == 'node':            cmd_node(args, fmt)
    elif args.cmd == 'creategroup':     cmd_creategroup(args, fmt)
    elif args.cmd == 'addtogroup':      cmd_addtogroup(args, fmt)
    elif args.cmd == 'removefromgroup': cmd_removefromgroup(args, fmt)
    elif args.cmd == 'groupshow':       cmd_groupshow(args, fmt)
    elif args.cmd == 'deletegroup':     cmd_deletegroup(args)
    elif args.cmd == 'groups':
        params = {}
        if getattr(args, 'ansible', False): params['ansible'] = 1
        if getattr(args, 'nagios',  False): params['nagios']  = 1
        resp = api_get('groups', params)
        rows = resp.get('data', [])
        if fmt == 'json':
            print(json.dumps(rows, indent=2, default=str))
        elif fmt == 'csv':
            _fmt_csv(rows)
        elif getattr(args, 'all_fields', False):
            _fmt_table(rows, ['id', 'name', 'is_ansible', 'is_nagios', 'description', 'node_count'])
        else:
            for r in rows:
                print(r['name'])
    else:                               cmd_list(args.cmd, fmt)


if __name__ == '__main__':
    main()
