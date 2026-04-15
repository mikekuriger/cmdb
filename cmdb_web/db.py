import pymysql
import pymysql.cursors
from flask import current_app, g


def get_db():
    if 'db' not in g:
        cfg = current_app.config
        g.db = pymysql.connect(
            host=cfg['DB_HOST'], port=cfg['DB_PORT'],
            user=cfg['DB_USER'], password=cfg['DB_PASS'],
            database=cfg['DB_NAME'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query(sql, params=None, one=False):
    cur = get_db().cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    return rows[0] if (one and rows) else rows


def execute(sql, params=None):
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params or ())
    db.commit()
    return cur.lastrowid, cur.rowcount
