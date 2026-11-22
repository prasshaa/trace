"""Read-only retrieval of the alias rows the matcher uses.

Reads only public.trace_role_aliases joined to public.trace_role_taxonomy (lookup tables). Runs in
the caller's connection inside a read-only transaction; nothing is written.
"""
from typing import Dict, List, Protocol

import psycopg
from psycopg.rows import dict_row

from app.services.role_mapping.models import AliasRow, RoleInfo

ALIAS_SQL = """
select a.alias_id, a.alias, a.alias_normalized, a.role_id, a.seniority_hint_code, a.context_required,
       a.default_confidence, a.mapping_rule,
       t.default_seniority_code as role_default_seniority_code, t.is_resolved as role_is_resolved,
       t.active as role_active
from public.trace_role_aliases a
join public.trace_role_taxonomy t on t.role_id = a.role_id
order by a.alias_id
"""


ROLE_SQL = """
select role_id, default_seniority_code, is_resolved, active from public.trace_role_taxonomy order by role_id
"""


class AliasRepository(Protocol):
    def alias_rows(self) -> List[AliasRow]: ...

    def role_rows(self) -> Dict[str, RoleInfo]: ...


class PostgresAliasRepository:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def _read(self, sql: str) -> List[dict]:
        """One query in its own read-only transaction."""
        if self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raise RuntimeError("the alias repository needs a connection with no open transaction")
        previous = (self._conn.read_only, self._conn.autocommit)
        self._conn.autocommit = False
        self._conn.read_only = True
        try:
            with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql)
                return cur.fetchall()
        finally:
            self._conn.read_only, self._conn.autocommit = previous

    def alias_rows(self) -> List[AliasRow]:
        """Every alias row with its role's default seniority, resolution and active flags."""
        return [AliasRow(**r) for r in self._read(ALIAS_SQL)]

    def role_rows(self) -> Dict[str, RoleInfo]:
        """Every taxonomy role (used to describe curated mappings in the agreement report)."""
        return {r["role_id"]: RoleInfo(**r) for r in self._read(ROLE_SQL)}
