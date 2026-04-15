import secrets
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from auth import authenticate_local, authenticate_ldap, admin_required
from db import query, execute

web_bp = Blueprint('web', __name__)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@web_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('web.nodes'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate_local(username, password) or \
               authenticate_ldap(username, password)
        if user:
            login_user(user, remember=True)
            return redirect(request.args.get('next') or url_for('web.nodes'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')


@web_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('web.login'))


# ---------------------------------------------------------------------------
# Main pages
# ---------------------------------------------------------------------------

@web_bp.route('/')
@login_required
def index():
    return redirect(url_for('web.nodes'))


@web_bp.route('/nodes')
@login_required
def nodes():
    return render_template('nodes.html', section='nodes')


@web_bp.route('/nodes/<int:node_id>')
@login_required
def node_detail(node_id):
    node = query(
        'SELECT n.id, n.name, n.hostname, n.vcenter_path, n.power_state, '
        '       n.cpus, n.memory_gb, n.purpose, n.landscape, n.app_name, '
        '       n.description, n.deployment, n.cmdb_uuid, n.first_seen, n.last_seen, n.active, n.is_template, '
        '       o.full_name AS guest_os, o.category AS os_category, '
        '       e.name AS environment, t.name AS tier, ow.name AS owner, '
        '       dc.path AS datacenter, vc.url AS vcenter_url, vc.label AS vcenter_label '
        'FROM nodes n '
        'LEFT JOIN operating_systems o ON o.id = n.os_id '
        'LEFT JOIN environments e ON e.id = n.environment_id '
        'LEFT JOIN tiers t ON t.id = n.tier_id '
        'LEFT JOIN owners ow ON ow.id = n.owner_id '
        'LEFT JOIN datacenters dc ON dc.id = n.datacenter_id '
        'LEFT JOIN vcenters vc ON vc.id = dc.vcenter_id '
        'WHERE n.id = %s',
        (node_id,), one=True
    )
    if not node:
        flash('Node not found.', 'warning')
        return redirect(url_for('web.nodes'))
    ips       = query('SELECT ip, is_primary, source FROM ip_addresses WHERE node_id=%s ORDER BY is_primary DESC', (node_id,))
    groups    = query('SELECT g.id, g.name, g.is_ansible, g.is_nagios FROM groups_ g JOIN node_groups ng ON ng.group_id=g.id WHERE ng.node_id=%s ORDER BY g.name', (node_id,))
    all_groups= query('SELECT id, name, is_ansible, is_nagios FROM groups_ ORDER BY name')
    attrs     = query('SELECT name, value FROM node_attributes WHERE node_id=%s ORDER BY name', (node_id,))
    tags      = query('SELECT tg.name FROM tags tg JOIN node_tags nt ON nt.tag_id=tg.id WHERE nt.node_id=%s ORDER BY tg.name', (node_id,))
    return render_template('node_detail.html', node=node, ips=ips,
                           groups=groups, all_groups=all_groups,
                           attrs=attrs, tags=tags, section='nodes')


@web_bp.route('/ips')
@login_required
def ips():
    return render_template('simple_table.html', section='ips', title='IP Addresses',
                           api_url='/api/v1/ips',
                           row_click='node',   # clicking IP row → /nodes/<node_id>
                           columns=[
                               {'title': 'IP',      'data': 'ip'},
                               {'title': 'Node',    'data': 'node_name'},
                               {'title': 'Primary', 'data': 'is_primary'},
                               {'title': 'Source',  'data': 'source'},
                           ])


# ── Object list views (all clickable → detail) ────────────────────────────────

def _obj_list(section, title, api_url, columns, detail_prefix):
    return render_template('simple_table.html', section=section, title=title,
                           api_url=api_url, columns=columns,
                           row_click='object', detail_prefix=detail_prefix)


@web_bp.route('/os')
@login_required
def os():
    return _obj_list('os', 'Operating Systems', '/api/v1/os', [
        {'title': 'OS Name',    'data': 'full_name'},
        {'title': 'Category',   'data': 'category'},
        {'title': 'Family',     'data': 'family'},
        {'title': 'Node Count', 'data': 'node_count'},
    ], '/os')


@web_bp.route('/os/<int:oid>')
@login_required
def os_detail(oid):
    obj = query('SELECT id, full_name, category, family FROM operating_systems WHERE id=%s',
                (oid,), one=True)
    if not obj:
        flash('Not found.', 'warning'); return redirect(url_for('web.os'))
    return render_template('object_detail.html', section='os',
        obj=obj, obj_type='os',
        back_url=url_for('web.os'), back_label='Operating Systems',
        page_title=obj['full_name'],
        edit_fields=[
            {'field': 'category', 'label': 'Category', 'type': 'select',
             'options': ['linux', 'windows', 'other']},
            {'field': 'family',   'label': 'Family',   'type': 'text'},
        ],
        node_filter='os_id', node_filter_val=oid,
    )


@web_bp.route('/environments')
@login_required
def environments():
    return _obj_list('environments', 'Environments', '/api/v1/environments', [
        {'title': 'Name',       'data': 'name'},
        {'title': 'Node Count', 'data': 'node_count'},
    ], '/environments')


@web_bp.route('/environments/<int:oid>')
@login_required
def environment_detail(oid):
    obj = query('SELECT id, name FROM environments WHERE id=%s', (oid,), one=True)
    if not obj:
        flash('Not found.', 'warning'); return redirect(url_for('web.environments'))
    return render_template('object_detail.html', section='environments',
        obj=obj, obj_type='environments',
        back_url=url_for('web.environments'), back_label='Environments',
        page_title=obj['name'],
        edit_fields=[{'field': 'name', 'label': 'Name', 'type': 'text'}],
        node_filter='env_id', node_filter_val=oid,
    )


@web_bp.route('/tiers')
@login_required
def tiers():
    return _obj_list('tiers', 'Tiers', '/api/v1/tiers', [
        {'title': 'Name',       'data': 'name'},
        {'title': 'Node Count', 'data': 'node_count'},
    ], '/tiers')


@web_bp.route('/tiers/<int:oid>')
@login_required
def tier_detail(oid):
    obj = query('SELECT id, name FROM tiers WHERE id=%s', (oid,), one=True)
    if not obj:
        flash('Not found.', 'warning'); return redirect(url_for('web.tiers'))
    return render_template('object_detail.html', section='tiers',
        obj=obj, obj_type='tiers',
        back_url=url_for('web.tiers'), back_label='Tiers',
        page_title=obj['name'],
        edit_fields=[{'field': 'name', 'label': 'Name', 'type': 'text'}],
        node_filter='tier_id', node_filter_val=oid,
    )


@web_bp.route('/owners')
@login_required
def owners():
    return _obj_list('owners', 'Owners', '/api/v1/owners', [
        {'title': 'Name',       'data': 'name'},
        {'title': 'Email',      'data': 'email'},
        {'title': 'Node Count', 'data': 'node_count'},
    ], '/owners')


@web_bp.route('/owners/<int:oid>')
@login_required
def owner_detail(oid):
    obj = query('SELECT id, name, email FROM owners WHERE id=%s', (oid,), one=True)
    if not obj:
        flash('Not found.', 'warning'); return redirect(url_for('web.owners'))
    return render_template('object_detail.html', section='owners',
        obj=obj, obj_type='owners',
        back_url=url_for('web.owners'), back_label='Owners',
        page_title=obj['name'],
        edit_fields=[
            {'field': 'name',  'label': 'Name',  'type': 'text'},
            {'field': 'email', 'label': 'Email', 'type': 'email'},
        ],
        node_filter='owner_id', node_filter_val=oid,
    )


@web_bp.route('/tags')
@login_required
def tags():
    return _obj_list('tags', 'Tags', '/api/v1/tags', [
        {'title': 'Name',       'data': 'name'},
        {'title': 'Category',   'data': 'category'},
        {'title': 'Node Count', 'data': 'node_count'},
    ], '/tags')


@web_bp.route('/tags/<int:oid>')
@login_required
def tag_detail(oid):
    obj = query('SELECT id, name, category FROM tags WHERE id=%s', (oid,), one=True)
    if not obj:
        flash('Not found.', 'warning'); return redirect(url_for('web.tags'))
    return render_template('object_detail.html', section='tags',
        obj=obj, obj_type='tags',
        back_url=url_for('web.tags'), back_label='Tags',
        page_title=obj['name'],
        edit_fields=[
            {'field': 'name',     'label': 'Name',     'type': 'text'},
            {'field': 'category', 'label': 'Category', 'type': 'text'},
        ],
        node_filter='tag_id', node_filter_val=oid,
    )


@web_bp.route('/groups')
@login_required
def groups():
    return render_template('groups.html', section='groups')


@web_bp.route('/groups/<int:gid>')
@login_required
def group_detail(gid):
    group = query('SELECT * FROM groups_ WHERE id=%s', (gid,), one=True)
    if not group:
        flash('Group not found.', 'warning')
        return redirect(url_for('web.groups'))
    member_count = query(
        'SELECT COUNT(*) AS c FROM node_groups WHERE group_id=%s', (gid,), one=True
    )['c']
    all_groups = query('SELECT id, name, is_ansible, is_nagios FROM groups_ ORDER BY name')
    return render_template('group_detail.html', group=group,
                           member_count=member_count,
                           all_groups=all_groups, section='groups')


@web_bp.route('/changes')
@login_required
def changes():
    return render_template('changes.html', section='changes')


@web_bp.route('/scan-runs')
@login_required
def scan_runs():
    return render_template('simple_table.html', section='scan_runs', title='Scan Runs',
                           api_url='/api/v1/scan-runs',
                           columns=[
                               {'title': 'ID',           'data': 'id'},
                               {'title': 'Source',       'data': 'source'},
                               {'title': 'Started',      'data': 'started_at'},
                               {'title': 'Finished',     'data': 'finished_at'},
                               {'title': 'Duration (s)', 'data': 'duration_secs'},
                           ])


# ---------------------------------------------------------------------------
# Admin — user management
# ---------------------------------------------------------------------------

@web_bp.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = query(
        'SELECT id, username, email, is_admin, is_ldap, active, last_login '
        'FROM users ORDER BY username'
    )
    return render_template('admin/users.html', users=users, section='admin')


@web_bp.route('/admin/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_new():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip() or None
        password = request.form.get('password', '')
        is_admin = 1 if request.form.get('is_admin') else 0
        is_ldap  = 1 if request.form.get('is_ldap')  else 0
        if not username:
            flash('Username required.', 'danger')
        else:
            pw_hash = generate_password_hash(password) if (password and not is_ldap) else None
            api_key = secrets.token_hex(32)
            try:
                execute(
                    'INSERT INTO users (username, email, password_hash, is_admin, is_ldap, api_key) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (username, email, pw_hash, is_admin, is_ldap, api_key)
                )
                flash(f'User {username} created.', 'success')
                return redirect(url_for('web.admin_users'))
            except Exception as e:
                flash(f'Error: {e}', 'danger')
    return render_template('admin/user_form.html', user=None, section='admin')


@web_bp.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_edit(uid):
    user = query('SELECT * FROM users WHERE id=%s', (uid,), one=True)
    if not user:
        flash('User not found.', 'warning')
        return redirect(url_for('web.admin_users'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip() or None
        is_admin = 1 if request.form.get('is_admin') else 0
        active   = 1 if request.form.get('active')   else 0
        password = request.form.get('password', '').strip()
        if password and not user['is_ldap']:
            execute(
                'UPDATE users SET email=%s, is_admin=%s, active=%s, password_hash=%s WHERE id=%s',
                (email, is_admin, active, generate_password_hash(password), uid)
            )
        else:
            execute(
                'UPDATE users SET email=%s, is_admin=%s, active=%s WHERE id=%s',
                (email, is_admin, active, uid)
            )
        flash('User updated.', 'success')
        return redirect(url_for('web.admin_users'))
    return render_template('admin/user_form.html', user=user, section='admin')


@web_bp.route('/admin/users/<int:uid>/regenerate-key', methods=['POST'])
@login_required
@admin_required
def admin_regen_key(uid):
    new_key = secrets.token_hex(32)
    execute('UPDATE users SET api_key=%s WHERE id=%s', (new_key, uid))
    flash(f'New API key: {new_key}', 'success')
    return redirect(url_for('web.admin_user_edit', uid=uid))
