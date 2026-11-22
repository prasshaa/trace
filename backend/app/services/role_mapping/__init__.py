"""Deterministic title-to-role matching against public.trace_role_aliases (M5, task 1).

Proposals only: nothing is written to the database, and review eligibility is unchanged.
See docs/ROLE_MAPPING.md.
"""
from app.services.role_mapping.matcher import (
    WHITESPACE, AliasDataError, AliasIndex, is_product_owner_title, match_title, normalize_title,
)
from app.services.role_mapping.models import AliasRow, RoleInfo, RoleOption, TitleMatch
from app.services.role_mapping.repository import AliasRepository, PostgresAliasRepository

__all__ = [
    "AliasDataError", "AliasIndex", "AliasRepository", "AliasRow", "PostgresAliasRepository", "RoleInfo",
    "RoleOption", "TitleMatch", "WHITESPACE", "is_product_owner_title", "match_title", "normalize_title",
]
