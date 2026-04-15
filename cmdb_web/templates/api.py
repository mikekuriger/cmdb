from flask import Blueprint, jsonify, request
from flask_login import current_user
from auth import api_auth_required, admin_required
from db import query, execute
from datetime import datetime

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

# ---------------------------------------------------------------------------
# Nodes query helpers
# ---------------------------------------------------------------------------

_NODE_SELECT = """
SELECT
    n.id, n.name, n.hostname, n.power_state, n.cpus, n.memory_gb,
    n.purpose, n.landscape, n.app_name, n.description,
    n.deployment, n.cmdb_uuid, n.vcenter_path,
    n.first_seen, n.last_seen, n.active,
    o.full_name  AS guest_os,
    o.category   AS os_category,
    e.name       AS environment,
    t.name       AS tier,
    ow.name      AS owner,
    (SELECT ip FROM ip_addresses WHERE node_id=n.id AND is_primary=1 LIMIT 1) AS primary_ip,
    dc.path      AS datacenter,
    vc.url       AS vcenter_url,
    vc.label     AS vcenter_label,
    GROUP_CONCAT(DISTINCT tg.name ORDER BY tg.name SEPARATOR '|') AS tags
"""

_NODE_FROM = """
FROM nodes n
LEFT JOIN operating_systems o  ON o.id  = n.os_id
LEFT JOIN environments      e  ON e.id  = n.environment_id
LEFT JOIN tiers             t  ON t.id  = n.tier_id
LEFT JOIN owners           ow  ON ow.id = n.owner_id
LEFT JOIN datacenters      dc  ON dc.id = n.datacenter_id
LEFT JOIN vcenters         vc  ON vc.id = dc.vcenter_id
LEFT JOIN node_tags        nt  ON nt.node_id = n.id
LEFT JOIN tags             tg  ON tg.id      = nt.tag_id
"""

# Maps DataTables column index → ORDER BY expression
_DT_SORT_COLS = [
    'n.name', 'n.hostname', 'n.power_state', 'o.full_name',
    'e.name', 't.name', 'ow.name', 'n.name', 'dc.path', 'n.last_seen',
]

# Allowed explicit sort keys for REST API (?sort=name)
_SORT_MAP = {
    'name': 'n.name', 'hostname': 'n.hostname', 'power_state': 'n.power_state',
    'guest_os': 'o.full_name', 'os_category': 'o.category',
    'environment': 'e.name', 'tier': 't.name', 'owner': 'ow.name',
    'datacenter': 'dc.path',
    'last_seen': 'n.last_seen', 'cpus': 'n.cpus', 'memory_gb': 'n.memory_gb',
}


def _build_where(params):
    conds, vals = ['1=1'], []

    active = params.get('active', '1')
    if active != 'all':
        conds.append('n.active = %s')
        vals.append(int(active))

    for param, col in [
        ('power',       'n.power_state'),
        ('os_category', 'o.category'),
        ('env',         'e.name'),
        ('tier',        't.name'),
        ('owner',       'ow.name'),
        ('datacenter',  'dc.path'),
        ('vcenter',     'vc.label'),
    ]:
        v = params.get(param)
        if v:
            conds.append(f'{col} = %s')
            vals.append(v)

    # ID-based filters (used by object detail pages)
    for param, col in [
        ('os_id',    'n.os_id'),
        ('env_id',   'n.environment_id'),
        ('tier_id',  'n.tier_id'),
        ('owner_id', 'n.owner_id'),
        ('tag_id',   None),
    ]:
        v = params.get(param)
        if v:
            if param == 'tag_id':
                conds.append(
                    'n.id IN (SELECT nt2.node_id FROM node_tags nt2 WHERE nt2.tag_id = %s)'
                )
            else:
                conds.append(f'{col} = %s')
            vals.append(int(v))

    tag = params.get('tag')
    if tag:
        conds.append(
            'n.id IN (SELECT nt2.node_id FROM node_tags nt2 '
            'JOIN tags tg2 ON tg2.id = nt2.tag_id WHERE tg2.name = %s)'
        )
        vals.append(tag)

    group = params.get('group')
    if group:
        conds.append(
            'n.id IN (SELECT ng.node_id FROM node_groups ng '
            'JOIN groups_ g ON g.id = ng.group_id WHERE g.name = %s)'
        )
        vals.append(group)

    q = (params.get('q') or params.get('search[value]') or '').strip()
    if q:
        conds.append(
            '(n.name LIKE %s OR n.hostname LIKE %s OR o.full_name LIKE %s '
            'OR EXISTS(SELECT 1 FROM ip_addresses WHERE node_id=n.id AND ip LIKE %s))'
        )
        like = f'%{q}%'
        vals += [like, like, like, like]

    return ' AND '.join(conds), vals


def _serialize_row(r):
    out = dict(r)
    for k in ('first_seen', 'last_seen'):
        if out.get(k) and isinstance(out[k], datetime):
            out[k] = out[k].strftime('%Y-%m-%d %H:%M')
    return out


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@api_bp.route('/nodes')
@api_auth_required
def nodes():
    p = request.args

    where, vals = _build_where(p)

    # Count total matching rows
    count_sql = f'SELECT COUNT(DISTINCT n.id) AS cnt {_NODE_FROM} WHERE {where}'
    total_filtered = query(count_sql, vals, one=True)['cnt']

    # Sort — DataTables sends order[0][column]/order[0][dir]; REST API sends ?sort=
    dt_col_idx = p.get('order[0][column]')
    if dt_col_idx is not None:
        sort_col = _DT_SORT_COLS[min(int(dt_col_idx), len(_DT_SORT_COLS) - 1)]
    else:
        sort_col = _SORT_MAP.get(p.get('sort', ''), 'n.name')
    sort_dir = 'DESC' if p.get('order[0][dir]') == 'desc' or p.get('dir') == 'desc' else 'ASC'

    length = int(p.get('length', p.get('limit', 25)))
    start  = int(p.get('start',  p.get('offset', 0)))
    if length < 0:
        length = 50000  # DataTables "All"

    sql = (f'{_NODE_SELECT} {_NODE_FROM} WHERE {where} '
           f'GROUP BY n.id ORDER BY {sort_col} {sort_dir} LIMIT %s OFFSET %s')
    rows = query(sql, vals + [length, start])
    data = [_serialize_row(r) for r in rows]

    draw = p.get('draw', type=int)
    if draw is not None:
        # DataTables server-side protocol
        total_all = query('SELECT COUNT(*) AS cnt FROM nodes WHERE active=1', one=True)['cnt']
        return jsonify(draw=draw, recordsTotal=total_all,
                       recordsFiltered=total_filtered, data=data)

    return jsonify(data=data, total=total_filtered)


@api_bp.route('/nodes/<int:node_id>')
@api_auth_required
def node_detail(node_id):
    row = query(
        f'{_NODE_SELECT} {_NODE_FROM} WHERE n.id = %s GROUP BY n.id',
        (node_id,), one=True
    )
    if not row:
        return jsonify(error='Not found'), 404
    ips    = query('SELECT ip, is_primary, source FROM ip_addresses WHERE node_id=%s', (node_id,))
    groups = query(
        'SELECT g.name, g.type FROM groups_ g '
        'JOIN node_groups ng ON ng.group_id = g.id WHERE ng.node_id = %s', (node_id,)
    )
    attrs  = query('SELECT name, value FROM node_attributes WHERE node_id=%s ORDER BY name', (node_id,))
    r = _serialize_row(row)
    r['ip_addresses'] = list(ips)
    r['groups']       = list(groups)
    r['attributes']   = list(attrs)
    return jsonify(r)


@api_bp.route('/nodes/<int:node_id>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def update_node(node_id):
    data = request.get_json(force=True) or {}
    allowed = {
        'purpose', 'landscape', 'app_name', 'description',
        'deployment', 'cmdb_uuid', 'owner_id', 'environment_id', 'tier_id',
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify(error='No valid fields to update'), 400
    set_clause = ', '.join([f'`{k}`=%s' for k in updates])
    execute(f'UPDATE nodes SET {set_clause} WHERE id=%s',
            list(updates.values()) + [node_id])
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

@api_bp.route('/ips')
@api_auth_required
def ips():
    p = request.args
    conds, vals = ['1=1'], []
    q = p.get('q', '').strip()
    if q:
        conds.append('(ip.ip LIKE %s OR n.name LIKE %s)')
        vals += [f'%{q}%', f'%{q}%']
    if p.get('node_id'):
        conds.append('ip.node_id = %s')
        vals.append(int(p['node_id']))
    where = ' AND '.join(conds)
    rows = query(
        f'SELECT ip.id, ip.ip, ip.is_primary, ip.source, n.name AS node_name, n.id AS node_id '
        f'FROM ip_addresses ip JOIN nodes n ON n.id = ip.node_id WHERE {where} '
        f'ORDER BY ip.ip LIMIT 1000', vals
    )
    return jsonify(data=list(rows))


def _object_get(table, id_col, fields, oid):
    row = query(f'SELECT {", ".join(fields)} FROM `{table}` WHERE {id_col}=%s', (oid,), one=True)
    if not row:
        return jsonify(error='Not found'), 404
    return jsonify(dict(row))


def _object_update(table, id_col, allowed_fields, oid):
    data    = request.get_json(force=True) or {}
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify(error='No valid fields'), 400
    set_clause = ', '.join([f'`{k}`=%s' for k in updates])
    execute(f'UPDATE `{table}` SET {set_clause} WHERE {id_col}=%s',
            list(updates.values()) + [oid])
    return jsonify(ok=True)


# ── OS ────────────────────────────────────────────────────────────────────────

@api_bp.route('/os/<int:oid>')
@api_auth_required
def os_detail(oid):
    return _object_get('operating_systems', 'id',
                       ['id','full_name','category','family'], oid)


@api_bp.route('/os/<int:oid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def os_update(oid):
    return _object_update('operating_systems', 'id', {'category', 'family'}, oid)


# ── Environments ──────────────────────────────────────────────────────────────

@api_bp.route('/environments/<int:oid>')
@api_auth_required
def environment_detail(oid):
    return _object_get('environments', 'id', ['id','name'], oid)


@api_bp.route('/environments/<int:oid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def environment_update(oid):
    return _object_update('environments', 'id', {'name'}, oid)


# ── Tiers ─────────────────────────────────────────────────────────────────────

@api_bp.route('/tiers/<int:oid>')
@api_auth_required
def tier_detail(oid):
    return _object_get('tiers', 'id', ['id','name'], oid)


@api_bp.route('/tiers/<int:oid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def tier_update(oid):
    return _object_update('tiers', 'id', {'name'}, oid)


# ── Owners ────────────────────────────────────────────────────────────────────

@api_bp.route('/owners/<int:oid>')
@api_auth_required
def owner_detail(oid):
    return _object_get('owners', 'id', ['id','name','email'], oid)


@api_bp.route('/owners/<int:oid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def owner_update(oid):
    return _object_update('owners', 'id', {'name', 'email'}, oid)


# ── Tags ──────────────────────────────────────────────────────────────────────

@api_bp.route('/tags/<int:oid>')
@api_auth_required
def tag_detail(oid):
    return _object_get('tags', 'id', ['id','name','category'], oid)


@api_bp.route('/tags/<int:oid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def tag_update(oid):
    return _object_update('tags', 'id', {'name', 'category'}, oid)


# ── Operating Systems list ────────────────────────────────────────────────────

@api_bp.route('/os')
@api_auth_required
def operating_systems():
    rows = query(
        'SELECT o.id, o.full_name, o.category, o.family, COUNT(n.id) AS node_count '
        'FROM operating_systems o LEFT JOIN nodes n ON n.os_id=o.id AND n.active=1 '
        'GROUP BY o.id ORDER BY o.category, o.full_name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/environments')
@api_auth_required
def environments():
    rows = query(
        'SELECT e.id, e.name, COUNT(n.id) AS node_count '
        'FROM environments e LEFT JOIN nodes n ON n.environment_id=e.id AND n.active=1 '
        'GROUP BY e.id ORDER BY e.name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/tiers')
@api_auth_required
def tiers():
    rows = query(
        'SELECT t.id, t.name, COUNT(n.id) AS node_count '
        'FROM tiers t LEFT JOIN nodes n ON n.tier_id=t.id AND n.active=1 '
        'GROUP BY t.id ORDER BY t.name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/owners')
@api_auth_required
def owners():
    rows = query(
        'SELECT ow.id, ow.name, ow.email, COUNT(n.id) AS node_count '
        'FROM owners ow LEFT JOIN nodes n ON n.owner_id=ow.id AND n.active=1 '
        'GROUP BY ow.id ORDER BY ow.name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/tags')
@api_auth_required
def tags():
    rows = query(
        'SELECT t.id, t.name, t.category, COUNT(nt.node_id) AS node_count '
        'FROM tags t LEFT JOIN node_tags nt ON nt.tag_id=t.id '
        'GROUP BY t.id ORDER BY t.name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/groups')
@api_auth_required
def groups():
    rows = query(
        'SELECT g.id, g.name, g.type, g.description, COUNT(ng.node_id) AS node_count '
        'FROM groups_ g LEFT JOIN node_groups ng ON ng.group_id=g.id '
        'GROUP BY g.id ORDER BY g.type, g.name'
    )
    return jsonify(data=list(rows))


@api_bp.route('/groups', methods=['POST'])
@api_auth_required
@admin_required
def create_group():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='name required'), 400
    gtype = data.get('type', 'custom')
    if gtype not in ('ansible', 'nagios', 'custom'):
        gtype = 'custom'
    desc = (data.get('description') or '').strip() or None
    try:
        gid = execute(
            'INSERT INTO groups_ (name, type, description) VALUES (%s,%s,%s)',
            (name, gtype, desc)
        )[0]
        return jsonify(id=gid, name=name, ok=True), 201
    except Exception as e:
        return jsonify(error=str(e)), 400


@api_bp.route('/groups/by-name/<path:name>')
@api_auth_required
def group_by_name(name):
    row = query('SELECT * FROM groups_ WHERE name=%s', (name,), one=True)
    if not row:
        return jsonify(error='Not found'), 404
    return jsonify(dict(row))


@api_bp.route('/groups/<int:gid>', methods=['PUT', 'PATCH'])
@api_auth_required
@admin_required
def update_group(gid):
    data = request.get_json(force=True) or {}
    updates = {}
    if 'name' in data:        updates['name']        = (data['name'] or '').strip()
    if 'description' in data: updates['description'] = data['description'] or None
    if 'type' in data and data['type'] in ('ansible', 'nagios', 'custom'):
        updates['type'] = data['type']
    if not updates:
        return jsonify(error='No valid fields'), 400
    set_clause = ', '.join([f'`{k}`=%s' for k in updates])
    execute(f'UPDATE groups_ SET {set_clause} WHERE id=%s', list(updates.values()) + [gid])
    return jsonify(ok=True)


@api_bp.route('/groups/<int:gid>', methods=['DELETE'])
@api_auth_required
@admin_required
def delete_group(gid):
    execute('DELETE FROM groups_ WHERE id=%s', (gid,))
    return jsonify(ok=True)


@api_bp.route('/groups/<int:gid>/nodes')
@api_auth_required
def group_nodes(gid):
    rows = query(
        f'{_NODE_SELECT} {_NODE_FROM} '
        'JOIN node_groups _ng ON _ng.node_id=n.id '
        'WHERE _ng.group_id=%s GROUP BY n.id ORDER BY n.name',
        (gid,)
    )
    return jsonify(data=[_serialize_row(r) for r in rows])


@api_bp.route('/groups/<int:gid>/nodes', methods=['POST'])
@api_auth_required
@admin_required
def add_nodes_to_group(gid):
    """
    Add nodes to a group.
    Body: {"names": ["web*", "db01", "db02"]}  — supports * wildcards
          {"ids": [1, 2, 3]}
    Returns: {"added": N, "matched": N, "not_found": [...]}
    """
    data = request.get_json(force=True) or {}
    added    = 0
    matched  = 0
    not_found = []

    if data.get('ids'):
        for nid in data['ids']:
            execute('INSERT IGNORE INTO node_groups (node_id, group_id) VALUES (%s,%s)',
                    (int(nid), gid))
            added += 1

    if data.get('names'):
        for raw in data['names']:
            raw = raw.strip()
            if not raw:
                continue
            sql_pat = raw.replace('*', '%').replace('?', '_')
            nodes = query('SELECT id FROM nodes WHERE name LIKE %s', (sql_pat,))
            if not nodes:
                not_found.append(raw)
            for node in nodes:
                matched += 1
                execute('INSERT IGNORE INTO node_groups (node_id, group_id) VALUES (%s,%s)',
                        (node['id'], gid))
                added += 1

    return jsonify(ok=True, added=added, matched=matched, not_found=not_found)


@api_bp.route('/groups/<int:gid>/nodes', methods=['DELETE'])
@api_auth_required
@admin_required
def remove_nodes_from_group(gid):
    """
    Remove nodes from a group.
    Body: {"names": ["web*"]} or {"ids": [1, 2, 3]}
    """
    data = request.get_json(force=True) or {}
    removed = 0

    if data.get('ids'):
        for nid in data['ids']:
            _, cnt = execute(
                'DELETE FROM node_groups WHERE group_id=%s AND node_id=%s', (gid, int(nid))
            )
            removed += cnt

    if data.get('names'):
        for raw in data['names']:
            sql_pat = raw.strip().replace('*', '%').replace('?', '_')
            nodes = query('SELECT id FROM nodes WHERE name LIKE %s', (sql_pat,))
            for node in nodes:
                _, cnt = execute(
                    'DELETE FROM node_groups WHERE group_id=%s AND node_id=%s',
                    (gid, node['id'])
                )
                removed += cnt

    return jsonify(ok=True, removed=removed)


@api_bp.route('/groups/<int:gid>/nodes/<int:node_id>', methods=['DELETE'])
@api_auth_required
@admin_required
def remove_node_from_group(gid, node_id):
    execute('DELETE FROM node_groups WHERE group_id=%s AND node_id=%s', (gid, node_id))
    return jsonify(ok=True)


# Node → group membership (from node side)
@api_bp.route('/nodes/<int:node_id>/groups', methods=['POST'])
@api_auth_required
@admin_required
def node_add_group(node_id):
    data = request.get_json(force=True) or {}
    gid  = data.get('group_id')
    if not gid:
        return jsonify(error='group_id required'), 400
    execute('INSERT IGNORE INTO node_groups (node_id, group_id) VALUES (%s,%s)',
            (node_id, int(gid)))
    return jsonify(ok=True)


@api_bp.route('/nodes/<int:node_id>/groups/<int:gid>', methods=['DELETE'])
@api_auth_required
@admin_required
def node_remove_group(node_id, gid):
    execute('DELETE FROM node_groups WHERE node_id=%s AND group_id=%s', (node_id, gid))
    return jsonify(ok=True)


@api_bp.route('/vcenters')
@api_auth_required
def vcenters():
    rows = query(
        'SELECT vc.id, vc.url, vc.label, COUNT(DISTINCT dc.id) AS datacenter_count '
        'FROM vcenters vc LEFT JOIN datacenters dc ON dc.vcenter_id=vc.id '
        'GROUP BY vc.id ORDER BY vc.label'
    )
    return jsonify(data=list(rows))


@api_bp.route('/scan-runs')
@api_auth_required
def scan_runs():
    rows = query(
        'SELECT id, source, started_at, finished_at, '
        'TIMESTAMPDIFF(SECOND, started_at, finished_at) AS duration_secs '
        'FROM scan_runs ORDER BY started_at DESC LIMIT 100'
    )
    def s(r):
        out = dict(r)
        for k in ('started_at', 'finished_at'):
            if out.get(k) and isinstance(out[k], datetime):
                out[k] = out[k].strftime('%Y-%m-%d %H:%M:%S')
        return out
    return jsonify(data=[s(r) for r in rows])


# ---------------------------------------------------------------------------
# Sidebar counts
# ---------------------------------------------------------------------------

@api_bp.route('/counts')
@api_auth_required
def counts():
    return jsonify(
        nodes      = query('SELECT COUNT(*) AS c FROM nodes WHERE active=1',  one=True)['c'],
        ips        = query('SELECT COUNT(*) AS c FROM ip_addresses',           one=True)['c'],
        os         = query('SELECT COUNT(*) AS c FROM operating_systems',      one=True)['c'],
        envs       = query('SELECT COUNT(*) AS c FROM environments',           one=True)['c'],
        tiers      = query('SELECT COUNT(*) AS c FROM tiers',                  one=True)['c'],
        owners     = query('SELECT COUNT(*) AS c FROM owners',                 one=True)['c'],
        tags       = query('SELECT COUNT(*) AS c FROM tags',                   one=True)['c'],
        groups     = query('SELECT COUNT(*) AS c FROM groups_',                one=True)['c'],
        scan_runs  = query('SELECT COUNT(*) AS c FROM scan_runs',              one=True)['c'],
    )
