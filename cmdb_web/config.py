import os

class Config:
    SECRET_KEY = os.environ.get('CMDB_SECRET_KEY', 'dev-secret-change-me-in-production')

    DB_HOST = os.environ.get('CMDB_HOST', '127.0.0.1')
    DB_PORT = int(os.environ.get('CMDB_PORT', '3306'))
    DB_USER = os.environ.get('CMDB_USER', 'root')
    DB_PASS = os.environ.get('CMDB_PASS', 'Pay4mysql!')
    DB_NAME = os.environ.get('CMDB_DB',   'cmdb')

    # LDAP — set LDAP_ENABLED=true and fill in the rest to activate
    LDAP_ENABLED      = os.environ.get('LDAP_ENABLED', 'false').lower() == 'true'
    LDAP_SERVER       = os.environ.get('LDAP_SERVER', '')
    LDAP_PORT         = int(os.environ.get('LDAP_PORT', '389'))
    LDAP_USE_SSL      = os.environ.get('LDAP_USE_SSL', 'false').lower() == 'true'
    LDAP_BIND_DN      = os.environ.get('LDAP_BIND_DN', '')       # service account DN
    LDAP_BIND_PASS    = os.environ.get('LDAP_BIND_PASS', '')
    LDAP_BASE_DN      = os.environ.get('LDAP_BASE_DN', '')
    LDAP_USER_FILTER  = os.environ.get('LDAP_USER_FILTER', '(sAMAccountName={username})')
    LDAP_EMAIL_ATTR   = os.environ.get('LDAP_EMAIL_ATTR', 'mail')
    LDAP_DISPLAY_ATTR = os.environ.get('LDAP_DISPLAY_ATTR', 'displayName')
