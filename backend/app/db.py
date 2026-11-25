"""Database connections.

connect()          the ADMIN / migration identity (DATABASE_URL). Scripts and tests only; the API never uses it.
connect_runtime()  the restricted runtime login (DATABASE_RUNTIME_URL), the only connection the API opens. Every new
                   connection is checked: the session identity must not be a superuser, must not have BYPASSRLS and
                   must not own any table in the application schemas. Otherwise it is closed and refused
                   (PrivilegedRuntimeIdentity): the API never runs on an over-privileged login.
"""
import psycopg

from app.config import Settings

APP_SCHEMAS = ("public", "trace_ref", "trace_ingest", "trace_review", "trace_util", "trace_private", "trace_ops", "trace_semantic")
IDENTITY_CHECK = """
select r.rolsuper, r.rolbypassrls,
       exists (select 1 from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
               where c.relowner = r.oid and n.nspname = any(%s)) as owns_tables
from pg_catalog.pg_roles r where r.rolname = session_user
"""


class DatabaseNotConfigured(Exception):
    pass


class PrivilegedRuntimeIdentity(Exception):
    """The runtime connection's login is privileged (superuser, BYPASSRLS or table owner). Refused."""


def connect(settings: Settings) -> psycopg.Connection:
    """The admin identity (DATABASE_URL). Not for request handling."""
    if settings.database_url is None:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    return psycopg.connect(settings.database_url.get_secret_value(), connect_timeout=settings.db_connect_timeout)


def connect_runtime(settings: Settings) -> psycopg.Connection:
    """The restricted runtime login (DATABASE_RUNTIME_URL), verified before use."""
    if settings.database_runtime_url is None:
        raise DatabaseNotConfigured("DATABASE_RUNTIME_URL is not set")
    conn = psycopg.connect(settings.database_runtime_url.get_secret_value(), connect_timeout=settings.db_connect_timeout)
    try:
        row = conn.execute(IDENTITY_CHECK, (list(APP_SCHEMAS),)).fetchone()
        conn.rollback()
        if row is None or any(row):
            raise PrivilegedRuntimeIdentity("the runtime database login is privileged")
    except BaseException:
        conn.close()
        raise
    return conn


def check_connection(settings: Settings) -> None:
    """Open a short-lived runtime connection and run a trivial query. Raises on failure."""
    with connect_runtime(settings) as conn:
        row = conn.execute("select 1").fetchone()
    if row is None or row[0] != 1:
        raise RuntimeError("unexpected result from database health query")
