"""Load a de-identified peer cohort CSV into trace_peer_cohort.profiles.

    cd backend && python -m scripts.load_peer_cohort --csv PATH --dsn-env DATABASE_URL --cohort-version india-pm-v3

The CSV is not read from the repository's real cohort. Pass a local file. The connection string is taken from the
named environment variable and is never printed. Embeddings are computed locally with gte-small. Rows are upserted
by a surrogate key of the cohort version and the row contents, then rows of that version missing from the file are
removed. Re-running the same file does not duplicate profiles.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys

import psycopg

from app.config import get_settings
from app.services.embeddings.provider import GteSmallOnnx
from app.services.peer_matching.text import COLUMNS, cohort_path_text, parse_row


def read_rows(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise SystemExit("the CSV columns do not match the cohort extract")
        return [parse_row(dict(row)) for row in reader]


def surrogate(version: str, row: dict) -> str:
    blob = version + "|" + "|".join(str(row[name]) for name in (
        "norm_seniority_group", "final_primary_prior_function", "final_entry_move", "final_first_pm_group",
        "final_months_to_first_pm_from_earliest_nonintern", "final_n_pre_pm_roles", "has_mba", "norm_edu_has_degree",
        "norm_city", "final_background_status", "final_n_roles", "final_n_pm_roles", "norm_career_start_year"))
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Load a de-identified peer cohort")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dsn-env", required=True, help="Name of the environment variable holding the admin DSN")
    parser.add_argument("--cohort-version", default="india-pm-v3")
    args = parser.parse_args(argv)
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        raise SystemExit(f"{args.dsn_env} is not set")
    rows = read_rows(args.csv)
    if not rows:
        raise SystemExit("the CSV has no data rows")
    texts = [cohort_path_text(row) for row in rows]
    vectors = GteSmallOnnx(get_settings().trace_embedding_model_dir).embed(texts)
    keys = []
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            for row, vector in zip(rows, vectors):
                key = surrogate(args.cohort_version, row)
                keys.append(key)
                conn.execute(
                    """insert into trace_peer_cohort.profiles (
                         profile_key, cohort_version, taxonomy_version, norm_seniority_group, norm_city,
                         final_background_status, final_primary_prior_function, final_prior_functions,
                         final_multi_function, final_prev_role_function, final_entry_move, final_first_pm_group,
                         final_first_pm_dated, final_months_to_first_pm_from_earliest_nonintern, final_has_history,
                         final_dated_role_before_first_pm, final_n_pre_pm_roles, final_n_roles, final_n_pm_roles,
                         norm_edu_has_degree, norm_career_start_year, has_mba, path_text, embedding)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::extensions.vector)
                       on conflict (profile_key) do update set
                         cohort_version = excluded.cohort_version,
                         norm_seniority_group = excluded.norm_seniority_group,
                         final_primary_prior_function = excluded.final_primary_prior_function,
                         final_entry_move = excluded.final_entry_move,
                         final_first_pm_group = excluded.final_first_pm_group,
                         final_months_to_first_pm_from_earliest_nonintern = excluded.final_months_to_first_pm_from_earliest_nonintern,
                         final_n_pre_pm_roles = excluded.final_n_pre_pm_roles,
                         has_mba = excluded.has_mba,
                         norm_edu_has_degree = excluded.norm_edu_has_degree,
                         path_text = excluded.path_text,
                         embedding = excluded.embedding""",
                    (key, args.cohort_version, row["taxonomy_version"], row["norm_seniority_group"], row["norm_city"],
                     row["final_background_status"], row["final_primary_prior_function"], row["final_prior_functions"],
                     row["final_multi_function"], row["final_prev_role_function"], row["final_entry_move"],
                     row["final_first_pm_group"], row["final_first_pm_dated"],
                     row["final_months_to_first_pm_from_earliest_nonintern"], row["final_has_history"],
                     row["final_dated_role_before_first_pm"], row["final_n_pre_pm_roles"], row["final_n_roles"],
                     row["final_n_pm_roles"], row["norm_edu_has_degree"], row["norm_career_start_year"], row["has_mba"],
                     texts[len(keys) - 1],
                     "[" + ",".join(f"{float(v):.7f}" for v in vector) + "]"))
            conn.execute(
                "delete from trace_peer_cohort.profiles where cohort_version = %s and not (profile_key = any(%s))",
                (args.cohort_version, keys))
    print(f"loaded {len(keys)} peer profiles for {args.cohort_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
