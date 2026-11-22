# Role mapping: deterministic matching, trusted persistence and candidate decisions (M5)

**Package:** `app/services/role_mapping/` (`models.py`, `matcher.py`, `repository.py`, `agreement.py`).

**Matcher (task 1): proposals only.** `role_mapping` itself writes nothing. Persistence and candidate decisions (task 2, data foundation v2.6.0) are below. Neither changes a record's review status: a pending record stays a barrier in sequencing.

## Design

| Part | Responsibility |
|---|---|
| `repository.PostgresAliasRepository` | Reads `public.trace_role_aliases` joined to `public.trace_role_taxonomy`, each query in its own read-only transaction. The only database access |
| `matcher.AliasIndex` | Groups the alias rows by normalised alias and validates them once. Pure |
| `matcher.match_title(title, index)` | Returns a `TitleMatch` proposal. Pure and deterministic |
| `agreement` | Compares proposals with the curated mappings of the 41 synthetic fixtures and renders the report. Pure, except `main` |

**Source of truth.** `trace_role_aliases` is the only source of automatic mappings. Matching is an exact comparison of the normalised title with `alias_normalized`. There is no fuzzy, partial, token, abbreviation, semantic or LLM matching.

**Normalisation contract v1.** One contract, implemented identically in `matcher.normalize_title` and the database function `trace_util.normalize_title`, independent of the database locale:

1. Every run of the 29 whitespace code points in `matcher.WHITESPACE` becomes one ASCII space: U+0009–U+000D, U+001C–U+001F, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F and U+3000.
2. Leading and trailing spaces are removed.
3. Only ASCII `A`–`Z` are lower-cased. No other character changes: punctuation, dashes, zero-width characters and non-ASCII letters are kept, so a look-alike such as the Kelvin sign is never folded into `k`.
4. An empty result is `None` / NULL.

`original_title` keeps the input exactly; `lookup_title` holds the normalised form. A character outside the contract is kept as it is, so the title matches no alias (aliases are plain text) and needs a candidate decision: unsupported input fails closed. Agreement is enforced three ways: the alias table has a CHECK that `alias_normalized` equals `trace_util.normalize_title(alias)`, the index refuses rows that differ from `normalize_title(alias)`, and a parity test compares Python and PostgreSQL on every code point from U+0001 to U+30FF and selected higher ones.

**Product Owner family.** `matcher.is_product_owner_title` and `trace_util.is_product_owner_title` apply the same deterministic rule to the normalised title: it contains the words `product owner`, bounded on each side by the start or end of the title or by a character other than `a`–`z` and `0`–`9`. It recognises "Product Owner", "Senior Product Owner", "Product Owner (Payments)", "Product Owner - Growth" and "Product Owner/Scrum Master"; it does not recognise "Product Ownership Lead", "Product Owners", "Product-Owner" or "PO". It never maps anything: it only decides whether a PM-ownership clarification is required.

**Outcomes.**

| Outcome | When | Proposal |
|---|---|---|
| `automatic` | Exactly one alias row matches, it is not context-required, and its role is resolved and active | `proposed_role_id` = the alias's role; `proposed_seniority_code` = its seniority hint, or none (the role's default then applies, as in `trace_model.py`); `mapping_status` = `auto_high_confidence`; `mapping_confidence` = the alias's `default_confidence`; `mapping_basis` = `alias_match` |
| `context_required` | The matching rows are context-required. A title that maps to several roles always is | No role, no confidence; `mapping_status` = `needs_review`; `options` lists every role with its confidence and mapping rule, highest confidence first. Never confirmed automatically |
| `unresolved` | No alias matches, or the title is empty | No role; `mapping_status` = `unresolved` |

`mapping_basis = 'alias_match'` marks every automatic proposal. A candidate's own choice (a later task) will use a different basis, so the two stay distinguishable.

**Fail-safe guards on the alias data.** The index raises `AliasDataError` if one normalised alias maps to the same role twice, or to several roles without every row being context-required. A non-context-required alias whose role is unresolved, or whose role is retired, is not proposed (`unresolved`); retired roles are also dropped from decision options. None of these occur in the current data.

## Agreement report

```bash
python -m app.services.role_mapping.agreement --out-dir <directory>
```

It writes `role_mapping_agreement.md` (summary and every employment) and `role_mapping_agreement.csv`. It reads the alias and taxonomy rows from the local database and the fixtures from `data-foundation/data/synthetic/synthetic_profiles.jsonl`. The curated fixture mappings are a comparison baseline, not a target: the matcher is not tuned to them. The synthetic titles were curated from the alias table, so agreement on them overstates coverage of real resume titles.

Result on the current data (41 profiles, 106 employments):

| Outcome | Count | Agreement with the curated role |
|---|---|---|
| `automatic` | 101 | 100 agree; 1 disagrees (SYN-011 "Project Manager": alias → `ROLE-PROJ`, curated `ROLE-PGM`). Seniority agrees for all 101 |
| `context_required` | 3 | "Product Owner" ×2 (options `ROLE-OWNER`, `PM-PM`), "Associate" ×1 (only option `ROLE-UNKNOWN`); the curated role is an option in all 3 |
| `unresolved` | 2 | "Product Consultant (Freelance)" (curated `ROLE-CONSULT`, `needs_review`) and "Product Management Intern (12-month programme)" (curated `PM-INTERN`, `confirmed`) |

## Persistence and candidate decisions (task 2, data foundation v2.6.0)

**Service operations** (`CandidateProfileService`, all owner-scoped through `owner_session`):

| Operation | What it does |
|---|---|
| `role_mapping_options(ctx, employment_id)` | Runs the matcher on the record's current title. Returns the outcome (`automatic`, `decision_required`, `unresolved`), the automatic proposal, the alias-derived options (eligible roles only) and then every other active, resolved role ("None of these"). A context-required alias whose only role is ineligible (`Associate` → `ROLE-UNKNOWN`) is `unresolved`. Each choice says whether it needs the Product Owner clarification. Changes nothing |
| `map_employment(ctx, employment_id)` | Applies the automatic mapping through `trace_review.apply_alias_mapping` when the outcome is `automatic`. Otherwise writes nothing and returns the options. A current candidate decision (`candidate_decision_kept`) or a mapping set outside the functions (`existing_mapping_kept`) is kept |
| `choose_role(ctx, employment_id, role_id, ownership_areas=None)` | The candidate's decision through `trace_review.choose_employment_role`: any active, resolved role. A Product Owner-family title (qualified or not) mapped to any product-management role needs at least one PM-ownership responsibility area (`ClarificationRequired`); areas elsewhere are refused. An automatic alias of a Product Owner-family title to a product-management role is offered as a decision, never applied |

The service checks every rule first for clear errors; the database checks them again and is authoritative (see `data-foundation/docs/ACCESS_CONTROL.md` § Role mapping).

**Stored provenance.** `role_mapping_basis` (`alias_match` or `candidate_selected`), `role_mapping_alias_id`, `mapped_title_raw` (the exact title mapped), `role_mapping_ownership_areas`, and the generated `mapping_is_stale`. The profile read model (`EmploymentRecord`) exposes all of them. `title_raw` is never changed by mapping.

**Confidence.** An automatic mapping keeps its alias confidence and is held to `min_mapping_confidence` (0.80). A candidate decision has no confidence (NULL): it is not an independently verified mapping, and it counts only for the candidate's own sequence.

**Stale mappings.** When `title_raw` changes after mapping (a draft edit or an M4 correction), `mapping_is_stale` becomes true. The mapping stays visible but sequencing excludes the record (`mapping_stale`) until `map_employment` or `choose_role` makes a fresh decision for the new title. Nothing is re-mapped silently.

**Fixture discrepancy (unchanged, pending source verification).** SYN-011 "Project Manager" is curated as `ROLE-PGM`, while the alias table and the taxonomy's canonical title give `ROLE-PROJ`. The matcher and the database follow the alias table; the fixture is not modified.

## Tests

- `tests/test_role_mapping_matcher.py` (no database): every outcome, multi-role ordering, seniority hints, empty titles, whitespace and case normalisation with the original preserved, no fuzzy matching, the data guards, and the real alias data from the data-foundation CSVs (no context-required alias is ever automatic).
- `tests/test_role_mapping_integration.py` (local database, read-only): the database rows equal the data-foundation sources, including the generated `alias_normalized`; the repository refuses an open transaction; the fixture report invariants.
- Normalisation contract and Product Owner family: unit tests for every contract whitespace character, characters outside the contract, look-alikes and title-family cases (`tests/test_role_mapping_matcher.py`); a Python/PostgreSQL parity test over every code point to U+30FF plus selected higher ones, and the alias CHECK (`tests/test_role_mapping_integration.py`).
- `tests/test_candidate_role_mapping_unit.py` (no database): options and outcomes, eligible roles, Product Owner flags, candidate-side sequencing rules (stale barrier, candidate decisions and the threshold).
- `tests/test_candidate_role_mapping_integration.py` (local database): automatic and context-required mapping, decisions from alias options and the full taxonomy, ineligible and retired roles, the Product Owner rule, review states, ownership, direct-SQL forgery, stale mappings after corrections and draft edits, races with corrections and deletions, and an owner-identity audit.
- `data-foundation/supabase/tests/database/trace_candidate_role_mapping.test.sql` (pgTAP): privileges, both functions and every refusal, CHECK constraints for the owner, staleness, M4 interplay and deletion.
