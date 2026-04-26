import secrets
from functools import wraps
from flask import current_app, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from db import query, execute

login_manager = LoginManager()
login_manager.login_view = 'web.login'
login_manager.login_message_category = 'warning'


class User(UserMixin):
    def __init__(self, row):
        self.id       = row['id']
        self.username = row['username']
        self.email    = row.get('email')
        self.is_admin = bool(row.get('is_admin', 0))
        self.is_ldap  = bool(row.get('is_ldap', 0))
        self.active   = bool(row.get('active', 1))
        self.api_key  = row.get('api_key')

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.active


@login_manager.user_loader
def load_user(user_id):
    row = query("SELECT * FROM users WHERE id=%s AND active=1", (user_id,), one=True)
    return User(row) if row else None


def authenticate_local(username, password):
    row = query(
        "SELECT * FROM users WHERE username=%s AND active=1 AND is_ldap=0",
        (username,), one=True
    )
    if row and check_password_hash(row['password_hash'], password):
        execute("UPDATE users SET last_login=NOW() WHERE id=%s", (row['id'],))
        return User(row)
    return None


def authenticate_ldap(username, password):
    from ldap3 import Server, Connection, SIMPLE, ALL
    cfg = current_app.config
    if not cfg['LDAP_ENABLED'] or not cfg['LDAP_SERVER']:
        return None
    try:
        server = Server(cfg['LDAP_SERVER'], port=cfg['LDAP_PORT'],
                        use_ssl=cfg['LDAP_USE_SSL'], get_info=ALL)
        svc = Connection(server, user=cfg['LDAP_BIND_DN'],
                         password=cfg['LDAP_BIND_PASS'], auto_bind=True)
        filt = cfg['LDAP_USER_FILTER'].format(username=username)
        svc.search(cfg['LDAP_BASE_DN'], filt,
                   attributes=[cfg['LDAP_EMAIL_ATTR'], 'distinguishedName'])
        if not svc.entries:
            return None
        user_dn    = svc.entries[0].entry_dn
        user_email = str(svc.entries[0][cfg['LDAP_EMAIL_ATTR']]) \
                     if cfg['LDAP_EMAIL_ATTR'] in svc.entries[0] else None
        # Bind as user to verify password
        conn = Connection(server, user=user_dn, password=password,
                          authentication=SIMPLE, auto_bind=True)
        if not conn.bound:
            return None
        # Upsert into local users table
        row = query("SELECT * FROM users WHERE username=%s", (username,), one=True)
        if row:
            execute("UPDATE users SET email=%s, last_login=NOW() WHERE username=%s",
                    (user_email, username))
            row['email'] = user_email
        else:
            uid = execute(
                "INSERT INTO users (username, email, is_ldap, is_admin, active, api_key) "
                "VALUES (%s,%s,1,0,1,%s)",
                (username, user_email, secrets.token_hex(32))
            )[0]
            row = query("SELECT * FROM users WHERE id=%s", (uid,), one=True)
        return User(row)
    except Exception as e:
        current_app.logger.error(f"LDAP error: {e}")
        return None


def get_user_by_api_key(key):
    row = query("SELECT * FROM users WHERE api_key=%s AND active=1", (key,), one=True)
    return User(row) if row else None


def api_auth_required(f):
    """Accept session login OR Authorization: Bearer <api-key>."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated:
            return f(*args, **kwargs)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            user = get_user_by_api_key(auth[7:].strip())
            if user:
                from flask_login import login_user
                login_user(user)
                return f(*args, **kwargs)
        return jsonify(error='Unauthorized'), 401
    return decorated


def api_read_allowed(f):
    """GET requests are public; token is accepted if provided but not required."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated:
            return f(*args, **kwargs)
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            user = get_user_by_api_key(auth[7:].strip())
            if user:
                from flask_login import login_user
                login_user(user)
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify(error='Forbidden — admin required'), 403
            flash('Admin access required.', 'danger')
            return redirect(url_for('web.index'))
        return f(*args, **kwargs)
    return decorated
