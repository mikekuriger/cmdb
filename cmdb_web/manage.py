#!/usr/bin/env python3
"""
manage.py — CMDB management commands.

Usage:
  python3 manage.py createuser admin --password secret --admin
  python3 manage.py createuser readonly_user --password secret
"""
import argparse, os, secrets, sys
import pymysql, pymysql.cursors
from werkzeug.security import generate_password_hash


def get_conn():
    return pymysql.connect(
        host=os.environ.get('CMDB_HOST', '127.0.0.1'),
        port=int(os.environ.get('CMDB_PORT', '3306')),
        user=os.environ.get('CMDB_USER', 'root'),
        password=os.environ.get('CMDB_PASS', 'Pay4mysql!'),
        database=os.environ.get('CMDB_DB', 'cmdb'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def cmd_createuser(args):
    conn = get_conn()
    cur  = conn.cursor()
    pw_hash = generate_password_hash(args.password) if args.password else None
    api_key = secrets.token_hex(32)
    cur.execute(
        'INSERT INTO users (username, email, password_hash, is_admin, api_key) '
        'VALUES (%s,%s,%s,%s,%s) '
        'ON DUPLICATE KEY UPDATE '
        '  password_hash = VALUES(password_hash), '
        '  is_admin      = VALUES(is_admin)',
        (args.username, args.email or None, pw_hash, 1 if args.admin else 0, api_key)
    )
    cur.execute('SELECT * FROM users WHERE username=%s', (args.username,))
    u = cur.fetchone()
    print(f"{'Created' if cur.rowcount else 'Updated'}: {u['username']}")
    print(f"  Admin  : {bool(u['is_admin'])}")
    print(f"  API Key: {u['api_key']}")
    conn.close()


def cmd_listusers(args):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute('SELECT id, username, email, is_admin, is_ldap, active, last_login FROM users ORDER BY username')
    for u in cur.fetchall():
        role = 'admin' if u['is_admin'] else 'user'
        typ  = 'ldap'  if u['is_ldap']  else 'local'
        print(f"  [{u['id']:3}] {u['username']:<30} {role:<6} {typ:<6} active={u['active']}  last_login={u['last_login']}")
    conn.close()


def main():
    p = argparse.ArgumentParser(prog='manage.py')
    sub = p.add_subparsers(dest='cmd', required=True)

    pu = sub.add_parser('createuser', help='Create or update a local user')
    pu.add_argument('username')
    pu.add_argument('--password', required=True)
    pu.add_argument('--email',    default='')
    pu.add_argument('--admin',    action='store_true', help='Grant admin access')

    sub.add_parser('listusers', help='List all users')

    args = p.parse_args()
    if args.cmd == 'createuser': cmd_createuser(args)
    elif args.cmd == 'listusers': cmd_listusers(args)


if __name__ == '__main__':
    main()
