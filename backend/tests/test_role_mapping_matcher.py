"""Unit tests for the deterministic title-to-role matcher (M5, task 1). No database."""
import csv
import pathlib
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.role_mapping import AliasDataError, AliasIndex, AliasRow, match_title, normalize_title
from app.services.role_mapping.agreement import compare_fixtures, summarize
from app.services.role_mapping.models import RoleInfo

FOUNDATION = pathlib.Path(__file__).resolve().parents[2] / "data-foundation"
DEFAULTS = {"PM-PM": "mid", "PM-APM": "associate", "PM-SPM": "senior", "ROLE-PROJ": "mid", "ROLE-OWNER": "mid",
            "ROLE-UNKNOWN": "unknown", "ROLE-SWE": "mid", "ROLE-PGM": "mid", "PM-RETIRED": "mid"}


def row(alias_id, alias, role_id, *, hint=None, ctx=False, conf="0.95", rule=None, resolved=True, active=True, normalized=None):
    return AliasRow(alias_id=alias_id, alias=alias, alias_normalized=normalized if normalized is not None else normalize_title(alias),
                    role_id=role_id, seniority_hint_code=hint, context_required=ctx, default_confidence=Decimal(conf),
                    mapping_rule=rule if rule or not ctx else "rule", role_default_seniority_code=DEFAULTS[role_id],
                    role_is_resolved=resolved, role_active=active)


ROWS = [
    row("ALS-0001", "Associate Product Manager", "PM-APM"),
    row("ALS-0003", "Product Manager", "PM-PM"),
    row("ALS-0011", "Senior Product Manager", "PM-SPM"),
    row("ALS-0004", "PM", "PM-PM", ctx=True, conf="0.5", rule="product context"),
    row("ALS-0005", "PM", "ROLE-PROJ", ctx=True, conf="0.4", rule="project context"),
    row("ALS-0035", "Product Owner", "ROLE-OWNER", ctx=True, conf="0.6"),
    row("ALS-0036", "Product Owner", "PM-PM", ctx=True, conf="0.5"),
    row("ALS-0042", "Associate", "ROLE-UNKNOWN", hint="associate", ctx=True, conf="0.3", resolved=False),
    row("ALS-0049", "Program Management", "ROLE-PGM", ctx=True, conf="0.6"),
    row("ALS-0051", "Senior Software Engineer", "ROLE-SWE", hint="senior"),
    row("ALS-0090", "Project Manager", "ROLE-PROJ"),
]
INDEX = AliasIndex(ROWS)


# ------------------------------------------------------------------ outcomes
def test_unique_unambiguous_alias_is_mapped_automatically_with_its_confidence():
    m = match_title("Product Manager", INDEX)
    assert (m.outcome, m.proposed_role_id, m.mapping_status, m.mapping_confidence) == ("automatic", "PM-PM", "auto_high_confidence", Decimal("0.95"))
    assert m.mapping_basis == "alias_match" and m.matched_alias_ids == ("ALS-0003",)


def test_absent_seniority_hint_proposes_no_override_and_reports_the_role_default():
    m = match_title("Product Manager", INDEX)
    assert m.proposed_seniority_code is None and m.effective_seniority_code == "mid"


def test_seniority_hint_becomes_the_proposed_override():
    m = match_title("Senior Software Engineer", INDEX)
    assert (m.proposed_role_id, m.proposed_seniority_code, m.effective_seniority_code) == ("ROLE-SWE", "senior", "senior")


def test_context_required_alias_with_one_role_is_never_automatic():
    m = match_title("Program Management", INDEX)
    assert (m.outcome, m.mapping_status) == ("context_required", "needs_review")
    assert m.proposed_role_id is None and m.mapping_confidence is None and m.mapping_basis is None
    assert [o.role_id for o in m.options] == ["ROLE-PGM"]
    assert m.mapping_rule == "rule"


def test_alias_with_several_roles_offers_all_of_them_in_a_fixed_order():
    m = match_title("PM", INDEX)
    assert (m.outcome, m.proposed_role_id, m.mapping_status) == ("context_required", None, "needs_review")
    assert [(o.role_id, o.default_confidence, o.mapping_rule) for o in m.options] == [
        ("PM-PM", Decimal("0.5"), "product context"), ("ROLE-PROJ", Decimal("0.4"), "project context")]
    assert "2 roles" in m.reason and m.matched_alias_ids == ("ALS-0004", "ALS-0005")
    # The order does not depend on the order the rows were read in.
    assert match_title("PM", AliasIndex(list(reversed(ROWS)))) == m


def test_context_required_generic_title_offers_only_the_unresolved_role():
    m = match_title("Associate", INDEX)
    assert m.outcome == "context_required" and m.proposed_role_id is None
    (o,) = m.options
    assert (o.role_id, o.role_is_resolved, o.seniority_hint_code, o.effective_seniority_code) == ("ROLE-UNKNOWN", False, "associate", "associate")


def test_missing_alias_stays_unresolved():
    m = match_title("Chief Vibes Officer", INDEX)
    assert (m.outcome, m.mapping_status, m.proposed_role_id, m.options, m.matched_alias_ids) == ("unresolved", "unresolved", None, (), ())
    assert m.lookup_title == "chief vibes officer"


@pytest.mark.parametrize("title", [None, "", "   ", "\t\n", "\u00a0"])
def test_empty_titles_are_unresolved_and_kept_as_given(title):
    m = match_title(title, INDEX)
    assert (m.outcome, m.lookup_title, m.original_title) == ("unresolved", None, title)


# ------------------------------------------------------------------ normalisation
@pytest.mark.parametrize("title", ["product manager", "PRODUCT MANAGER", "  Product   Manager ", "Product\tManager",
                                   "Product\nManager", "Product\u00a0Manager", "\u2003Product Manager\u2003"])
def test_case_and_whitespace_are_normalised_but_the_original_title_is_preserved(title):
    m = match_title(title, INDEX)
    assert (m.outcome, m.proposed_role_id, m.lookup_title) == ("automatic", "PM-PM", "product manager")
    assert m.original_title == title


@pytest.mark.parametrize("title", ["Product Managers", "Sr. Product Manager", "Product Manager - Payments", "Product-Manager",
                                   "Product Manager (Payments)", "ProductManager", "Senior  Product Manager II",
                                   "Associate Product Mgr", "P.M."])
def test_no_fuzzy_partial_or_punctuation_matching(title):
    assert match_title(title, INDEX).outcome == "unresolved"


def test_normalize_title_is_idempotent_and_does_not_touch_punctuation():
    assert normalize_title("  UI/UX   Design Intern ") == "ui/ux design intern"
    assert normalize_title("Director – Product Management") == "director – product management"
    assert normalize_title(normalize_title(" A  b ")) == "a b"


# ------------------------------------------------------------------ fail-safe guards on the alias data
def test_alias_mapping_to_an_unresolved_role_is_not_automatic():
    idx = AliasIndex([row("ALS-0900", "Generalist", "ROLE-UNKNOWN", conf="0.9", resolved=False)])
    m = match_title("Generalist", idx)
    assert (m.outcome, m.proposed_role_id, m.mapping_status) == ("unresolved", None, "unresolved")


def test_alias_of_a_retired_role_is_not_used():
    idx = AliasIndex([row("ALS-0901", "Old Title", "PM-RETIRED", active=False)])
    assert match_title("Old Title", idx).outcome == "unresolved"
    idx = AliasIndex([row("ALS-0902", "Mixed", "PM-RETIRED", ctx=True, active=False), row("ALS-0903", "Mixed", "PM-PM", ctx=True)])
    assert [o.role_id for o in match_title("Mixed", idx).options] == ["PM-PM"]


def test_index_refuses_a_multi_role_alias_that_is_not_context_required_everywhere():
    with pytest.raises(AliasDataError, match="not context-required"):
        AliasIndex([row("ALS-0910", "Lead", "PM-PM", ctx=True), row("ALS-0911", "Lead", "ROLE-SWE")])


def test_index_refuses_a_normalised_alias_that_differs_from_the_matcher():
    with pytest.raises(AliasDataError, match="alias_normalized"):
        AliasIndex([row("ALS-0920", "Product Manager", "PM-PM", normalized="product  manager")])


def test_index_refuses_the_same_alias_twice_for_one_role():
    with pytest.raises(AliasDataError, match="same role"):
        AliasIndex([row("ALS-0930", "Product Manager", "PM-PM"), row("ALS-0931", "PRODUCT MANAGER", "PM-PM")])


def test_results_are_immutable_and_reject_unknown_fields():
    m = match_title("Product Manager", INDEX)
    with pytest.raises(ValidationError):
        m.proposed_role_id = "PM-SPM"
    with pytest.raises(ValidationError):
        AliasRow(**{**ROWS[0].model_dump(), "extra": 1})


# ------------------------------------------------------------------ the real alias data, offline (data-foundation CSV sources)
def _db_normalized(alias):          # lower(regexp_replace(btrim(alias), '\s+', ' ', 'g'))
    return re.sub(r"\s+", " ", alias.strip(" ")).lower()


def csv_index(additions: bool = False):
    """The alias rows of the data foundation's canonical source. With additions: also the rows added by later migrations
    (v2.32.0: data/taxonomy/role_aliases_additions.csv, the source of migration 20261122100000), as the database holds."""
    taxonomy = {r["role_id"]: r for r in csv.DictReader(open(FOUNDATION / "data/taxonomy/role_taxonomy.csv", encoding="utf-8"))}
    files = ["data/taxonomy/role_aliases.csv"] + (["data/taxonomy/role_aliases_additions.csv"] if additions else [])
    rows = [AliasRow(alias_id=r["alias_id"], alias=r["alias"], alias_normalized=_db_normalized(r["alias"]), role_id=r["role_id"],
                     seniority_hint_code=r["seniority_hint_code"] or None, context_required=r["context_required"] == "true",
                     default_confidence=Decimal(r["default_confidence"]), mapping_rule=r["mapping_rule"] or None,
                     role_default_seniority_code=taxonomy[r["role_id"]]["default_seniority_code"],
                     role_is_resolved=taxonomy[r["role_id"]]["is_resolved"] == "true", role_active=taxonomy[r["role_id"]]["active"] == "true")
            for f in files for r in csv.DictReader(open(FOUNDATION / f, encoding="utf-8"))]
    roles = {k: RoleInfo(role_id=k, default_seniority_code=v["default_seniority_code"], is_resolved=v["is_resolved"] == "true",
                         active=v["active"] == "true") for k, v in taxonomy.items()}
    return rows, AliasIndex(rows), roles


def test_the_alias_dataset_passes_the_index_rules():
    rows, idx, _ = csv_index()
    assert len(idx) == len(rows) == 106
    assert set(idx.multi_role_aliases()) == {"pm", "product lead", "product owner", "tpm", "product & growth", "product consultant"}


def test_no_context_required_alias_is_ever_automatic():
    rows, idx, _ = csv_index()
    for r in rows:
        m = match_title(r.alias, idx)
        if r.context_required:
            assert m.outcome == "context_required" and m.proposed_role_id is None, r.alias_id
        else:
            assert (m.outcome, m.proposed_role_id, m.mapping_confidence) == ("automatic", r.role_id, r.default_confidence), r.alias_id


def test_fixture_report_covers_every_employment_and_never_hides_a_disagreement():
    import json
    _, idx, roles = csv_index()
    profiles = [json.loads(l) for l in open(FOUNDATION / "data/synthetic/synthetic_profiles.jsonl", encoding="utf-8") if l.strip()]
    rows = compare_fixtures(profiles, idx, roles)
    s = summarize(rows, idx)
    assert (s["profiles"], s["employments"]) == (41, 106)
    assert sum(s["outcomes"].values()) == sum(s["role_agreement"].values()) == 106
    for r in rows:
        assert r.match.original_title == next(e for p in profiles for e in p["employments"]
                                              if e["employment_id"] == r.employment_id)["title_raw_redacted"]
        if r.match.outcome == "automatic" and r.match.proposed_role_id != r.curated_role_id:
            assert r.role_agreement == "disagree"


# ------------------------------------------------------------------ normalisation contract v1 (pure)
from app.services.role_mapping import WHITESPACE, is_product_owner_title  # noqa: E402


def test_the_contract_whitespace_set_is_exactly_29_code_points():
    assert len(WHITESPACE) == len(set(WHITESPACE)) == 29
    assert sorted(WHITESPACE) == sorted(chr(c) for c in range(0x110000) if chr(c).isspace())   # Python 3 str.isspace today


@pytest.mark.parametrize("ws", list(WHITESPACE))
def test_every_contract_whitespace_character_becomes_one_ascii_space(ws):
    assert normalize_title(f"{ws}Product{ws}{ws}Manager{ws}") == "product manager"
    assert match_title(f"Product{ws}Manager", INDEX).proposed_role_id == "PM-PM"


@pytest.mark.parametrize("title, expected", [
    ("Product\u200bManager", "product\u200bmanager"),        # zero-width space is not whitespace: kept
    ("Product\ufeffManager", "product\ufeffmanager"),        # byte-order mark: kept
    ("Product\u180eManager", "product\u180emanager"),        # Mongolian vowel separator: kept
    ("Product\x01Manager", "product\x01manager"),            # control character: kept
    ("\u212aey Account", "\u212aey account"),                 # Kelvin sign: not folded into ASCII k
    ("\u0130stanbul PM", "\u0130stanbul pm"),                # dotted capital I: unchanged
    ("\u00c4\u00c9 Lead", "\u00c4\u00c9 lead"),                # non-ASCII capitals: unchanged
    ("Director \u2013 Product", "director \u2013 product"),    # en dash kept
    ("UI/UX (Design) Intern!", "ui/ux (design) intern!"),     # punctuation kept
])
def test_characters_outside_the_contract_are_kept(title, expected):
    assert normalize_title(title) == expected


def test_non_ascii_lookalikes_never_match_an_alias():
    idx = AliasIndex([row("ALS-0084", "Key Account Manager", "ROLE-SWE")])
    assert match_title("\u212aey Account Manager", idx).outcome == "unresolved"
    assert match_title("KEY ACCOUNT MANAGER", idx).outcome == "automatic"


@pytest.mark.parametrize("title", ["Product Owner", "Senior Product Owner", "Product Owner (Payments)", "Product Owner - Growth",
                                   "PRODUCT OWNER", "product   owner", "Lead\u00a0Product\u3000Owner", "(Product Owner)",
                                   "Product Owner/Scrum Master", "Product Owner, Payments", "Sr.Product Owner", "Product Owner:Payments",
                                   "Product Owner II", "\u00c9quipe Product Owner"])
def test_product_owner_family_titles_are_recognised(title):
    assert is_product_owner_title(title)


@pytest.mark.parametrize("title", [None, "", "Product Ownership Lead", "Product Owners", "Product-Owner", "ProductOwner",
                                   "Product Manager", "Owner", "Product Owner2", "XProduct Owner", "Product\u200bOwner", "Owner Product"])
def test_other_titles_are_not_product_owner_family(title):
    assert not is_product_owner_title(title)
