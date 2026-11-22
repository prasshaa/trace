"""Deterministic title-to-role matching against public.trace_role_aliases (M5, task 1).

Pure logic: no database, no network, no randomness. Alias rows are read separately
(``repository.read_alias_rows``) and passed in, so matching is testable with in-memory rows.

Rules (data foundation §9, M5 product decisions):

* The alias table is the only source of automatic mappings. Matching is an exact comparison of the
  normalised title with ``alias_normalized``. There is no fuzzy, partial, token, abbreviation or
  semantic matching: "Sr. Product Manager", "Product Managers" and "Product Manager - Payments" do not
  match "Product Manager".
* Normalisation follows the title normalisation contract v1, implemented identically in the database
  (``trace_util.normalize_title``) and independent of any locale: every run of the 29 whitespace code
  points in ``WHITESPACE`` becomes one ASCII space, leading and trailing spaces are removed, and only
  ASCII A-Z are lower-cased. Punctuation, dashes and every other character are kept, so nothing (for
  example the Kelvin sign) is folded into ASCII; such a title matches no alias and needs a decision.
  The original title is kept exactly as given.
* One matching alias row, not context-required, whose role is resolved and active: ``automatic``,
  with the alias's role, its seniority hint as the seniority override (none if absent) and its
  ``default_confidence``; status ``auto_high_confidence``.
* Context-required rows: ``context_required``, status ``needs_review``, no proposed role. A title that
  maps to several roles is always context-required (the index refuses data where it is not).
* No match, or an empty title: ``unresolved``.
"""
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from app.services.role_mapping.models import AliasRow, RoleOption, TitleMatch


class AliasDataError(ValueError):
    """The alias rows break a rule the matcher relies on. The matcher refuses to run on them."""


# Title normalisation contract v1 (same list in trace_util.normalize_title).
WHITESPACE = ("\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020\u0085\u00a0\u1680"
              "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")
_WHITESPACE_RUN = re.compile("[" + re.escape(WHITESPACE) + "]+")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_PRODUCT_OWNER = re.compile(r"(?:^|[^a-z0-9])product owner(?:$|[^a-z0-9])")


def normalize_title(title: Optional[str]) -> Optional[str]:
    """Lookup form of a title under contract v1: whitespace runs (``WHITESPACE``) become one ASCII space,
    the ends are trimmed, ASCII A-Z are lower-cased, nothing else changes. ``None`` for a missing or
    whitespace-only title."""
    if title is None:
        return None
    lookup = _WHITESPACE_RUN.sub(" ", title).translate(_ASCII_LOWER).strip(" ")
    return lookup or None


def is_product_owner_title(title: Optional[str]) -> bool:
    """Product Owner family, detected from the words alone (same rule as trace_util.is_product_owner_title):
    the normalised title contains "product owner" bounded by the title's ends or by characters other than
    a-z and 0-9. Recognises qualified titles ("Product Owner (Payments)", "Senior Product Owner"); does
    not recognise "Product Ownership" or "Product-Owner". It never maps anything."""
    lookup = normalize_title(title)
    return bool(lookup and _PRODUCT_OWNER.search(lookup))


def _option(row: AliasRow) -> RoleOption:
    return RoleOption(alias_id=row.alias_id, alias=row.alias, role_id=row.role_id,
                      seniority_hint_code=row.seniority_hint_code,
                      effective_seniority_code=row.seniority_hint_code or row.role_default_seniority_code,
                      default_confidence=row.default_confidence, mapping_rule=row.mapping_rule,
                      role_is_resolved=row.role_is_resolved)


class AliasIndex:
    """Alias rows grouped by normalised alias, validated once.

    Refuses (``AliasDataError``) rows where:
      * ``alias_normalized`` differs from ``normalize_title(alias)`` (the database and the matcher
        would disagree about what matches);
      * one normalised alias maps to the same role twice;
      * one normalised alias maps to several roles and any of those rows is not context-required.
    """

    def __init__(self, rows: Iterable[AliasRow]):
        groups: Dict[str, List[AliasRow]] = defaultdict(list)
        for row in rows:
            if normalize_title(row.alias) != row.alias_normalized:
                raise AliasDataError(f"{row.alias_id}: alias_normalized {row.alias_normalized!r} differs from the "
                                     f"matcher's normalisation {normalize_title(row.alias)!r}")
            groups[row.alias_normalized].append(row)
        for lookup, group in groups.items():
            roles = [r.role_id for r in group]
            if len(roles) != len(set(roles)):
                raise AliasDataError(f"alias {lookup!r} maps to the same role more than once")
            if len(group) > 1 and not all(r.context_required for r in group):
                raise AliasDataError(f"alias {lookup!r} maps to {len(group)} roles but is not context-required in every row")
        # Deterministic order: highest default confidence first, then role id.
        self._groups: Dict[str, Tuple[AliasRow, ...]] = {
            k: tuple(sorted(v, key=lambda r: (-r.default_confidence, r.role_id))) for k, v in groups.items()}

    def __len__(self) -> int:
        return sum(len(g) for g in self._groups.values())

    def lookup(self, lookup_title: str) -> Tuple[AliasRow, ...]:
        return self._groups.get(lookup_title, ())

    def multi_role_aliases(self) -> Dict[str, Tuple[str, ...]]:
        """Normalised aliases that map to more than one role, with their role ids."""
        return {k: tuple(r.role_id for r in g) for k, g in self._groups.items() if len(g) > 1}


def match_title(title: Optional[str], index: AliasIndex) -> TitleMatch:
    """Propose a role for one title. Deterministic; never writes anything."""
    lookup = normalize_title(title)
    if lookup is None:
        return TitleMatch(original_title=title, lookup_title=None, outcome="unresolved",
                          reason="the title is empty", mapping_status="unresolved")
    rows = index.lookup(lookup)
    if not rows:
        return TitleMatch(original_title=title, lookup_title=lookup, outcome="unresolved",
                          reason="no alias matches the title exactly", mapping_status="unresolved")
    ids = tuple(r.alias_id for r in rows)
    usable = tuple(r for r in rows if r.role_active)
    if not usable:
        return TitleMatch(original_title=title, lookup_title=lookup, outcome="unresolved", matched_alias_ids=ids,
                          reason="every matching alias maps to a retired role", mapping_status="unresolved")
    options = tuple(_option(r) for r in usable)
    if any(r.context_required for r in usable):
        reason = (f"the title maps to {len(usable)} roles; a decision is required" if len(usable) > 1
                  else "the alias is context-required; a decision is required")
        return TitleMatch(original_title=title, lookup_title=lookup, outcome="context_required", reason=reason,
                          matched_alias_ids=ids, options=options, mapping_status="needs_review",
                          mapping_rule=usable[0].mapping_rule)
    (row,) = usable          # a non-context-required alias maps to exactly one role (AliasIndex)
    if not row.role_is_resolved:
        return TitleMatch(original_title=title, lookup_title=lookup, outcome="unresolved", matched_alias_ids=ids,
                          options=options, reason=f"the alias maps to the unresolved role {row.role_id}",
                          mapping_status="unresolved")
    option = options[0]
    return TitleMatch(original_title=title, lookup_title=lookup, outcome="automatic",
                      reason="exactly one alias matches and it is not context-required",
                      matched_alias_ids=ids, options=options, proposed_role_id=row.role_id,
                      proposed_seniority_code=row.seniority_hint_code,
                      effective_seniority_code=option.effective_seniority_code,
                      mapping_status="auto_high_confidence", mapping_confidence=row.default_confidence,
                      mapping_basis="alias_match", mapping_rule=row.mapping_rule)


# --------------------------------------------------------------------------- suggestions (Phase 5, D50)
# A SUGGESTION is never a mapping: it preselects a role for the candidate to confirm (candidate_selected), and the
# automatic path above stays exact. A title gets a suggestion when one of its parts (split at "&", "and", "/", ",", "|"
# or a dash) ENDS with a known, non-context-required alias of at least two words (or a one-word alias such as
# "founder"), and the words before it are only descriptors ("Software", "Global", "AI"): never a level word that would
# change the role ("Senior", "Lead", "Head", "Associate", ...). Parts that suggest different roles suggest nothing.
LEVEL_WORDS = frozenset("""senior sr sr. junior jr jr. lead principal staff head director vp chief associate assistant
    deputy group intern trainee apprentice executive manager""".split())
_PART_SPLIT = re.compile(r"\s*(?:&|\band\b|/|,|\||\s[-–—]\s)\s*")


def suggest_role(title: Optional[str], index: AliasIndex) -> Optional[Tuple[AliasRow, str]]:
    """(alias row, the matched words of the title) or None. Deterministic; never writes anything."""
    lookup = normalize_title(title)
    if lookup is None or index.lookup(lookup):
        return None                                   # empty, or an exact alias decides (automatic or a decision)
    found: Dict[str, Tuple[AliasRow, str]] = {}
    for part in (p.strip(" ()") for p in _PART_SPLIT.split(lookup)):
        words = part.split(" ")
        for i in range(len(words)):                   # longest tail first
            tail = " ".join(words[i:])
            rows = [r for r in index.lookup(tail) if r.role_active and r.role_is_resolved]
            if len(rows) != 1 or rows[0].context_required:
                continue
            if any(w.strip(".") in LEVEL_WORDS for w in words[:i]):
                break                                 # "Senior QA Engineer" is not "QA Engineer"
            if rows[0].role_id.startswith("PM-") and is_product_owner_title(part):
                break
            found.setdefault(rows[0].role_id, (rows[0], tail))
            break
    return next(iter(found.values())) if len(found) == 1 else None
