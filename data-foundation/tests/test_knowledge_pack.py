"""The approved knowledge pack: the seed migration is generated from the reviewed JSON, and the JSON keeps the approved
rules (D16, D22-D26). Nothing here touches a database."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = json.loads((ROOT / "knowledge_packs/pm-v1.json").read_text(encoding="utf-8"))


class KnowledgePack(unittest.TestCase):
    def test_seed_migration_matches_the_reviewed_json(self):
        r = subprocess.run([sys.executable, "scripts/build_knowledge_pack.py", "--check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_sources_and_requirements_are_the_approved_set(self):
        keys = [s["key"] for s in PACK["sources"]]
        self.assertEqual(keys, ["F1", "F3", "F4"] + [f"J{i}" for i in range(1, 13)])
        self.assertFalse(any("sfia" in (s["url"] + s["publisher"]).lower() for s in PACK["sources"]))           # D16
        self.assertEqual(sorted(r["key"] for r in PACK["requirements"]),
                         sorted(["C1", "C2", "C3", "C4", "C5", "C6", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "L1", "L2", "L3", "L4"]))

    def test_every_evidence_row_names_a_pack_source_and_an_approved_stance(self):
        keys = {s["key"] for s in PACK["sources"]}
        for r in PACK["requirements"]:
            for e in r["evidence"]:
                self.assertIn(e["source"], keys, r["key"])
                self.assertIn(e["stance"], {"supports", "qualifies", "disagrees", "silent"})

    def test_postings_carry_stance_quote_locator_and_dated_status(self):
        for s in PACK["sources"]:
            if s["kind"] != "job_posting":
                self.assertNotIn("entry_background_stance", s)
                continue
            self.assertIn(s["entry_background_stance"], {"accepts_adjacent", "requires_target_role_experience",
                                                          "excludes_specified_transitions", "not_specified"})
            self.assertTrue(s["entry_locator"] and s["last_verified_at"])
            if s["entry_background_stance"] != "not_specified":
                self.assertTrue(s["entry_quote"] and len(s["entry_quote"]) <= 400)
        j12 = next(s for s in PACK["sources"] if s["key"] == "J12")
        self.assertEqual((j12["status"], j12["revalidate_by"]), ("expired", None))                             # D22: dated evidence only

    def test_no_synthetic_or_personal_data_in_the_pack(self):
        text = json.dumps(PACK)
        for bad in ("SYN-", "synthetic_test", "EVAL-", "@"):
            self.assertNotIn(bad, text)


PACK11 = json.loads((ROOT / "knowledge_packs/pm-v1.1.json").read_text(encoding="utf-8"))
NEW_AREAS = {"ai_product_work", "data_governance_quality", "business_case_economics", "executive_communication"}


class KnowledgePackV11(unittest.TestCase):
    """pm-v1.1 (Phase 3.1): only the mapping changes; the source inventory stays pm-v1's."""

    def test_reuses_the_pm_v1_source_inventory(self):
        self.assertEqual(PACK11["sources_from"], "pm-v1")
        self.assertNotIn("sources", PACK11)
        keys = {s["key"] for s in PACK["sources"]}
        for r in PACK11["requirements"]:
            for e in r["evidence"]:
                self.assertIn(e["source"], keys, r["key"])

    def test_same_requirements_and_evidence_as_pm_v1(self):
        old = {r["key"]: r for r in PACK["requirements"]}
        self.assertEqual(sorted(old), sorted(r["key"] for r in PACK11["requirements"]))
        for r in PACK11["requirements"]:
            self.assertEqual(r["evidence"], old[r["key"]]["evidence"], r["key"])
            self.assertEqual(r["description"], old[r["key"]]["description"], r["key"])

    def test_approved_conditions(self):
        conds = {r["key"]: r.get("area_conditions", {}) for r in PACK11["requirements"] if r.get("area_conditions")}
        self.assertEqual(conds, {"C4": {"business_case_economics": "tracks_outcomes"},
                                 "L1": {"strategy_portfolio": "multi_product_scope"},
                                 "L2": {"people_leadership": "coaching_mentoring"},
                                 "L4": {"delivery_execution": "cross_team"}})

    def test_new_areas_are_used_and_taxonomy_version_is_two(self):
        self.assertEqual(PACK11["taxonomy_version"], 2)
        used = set()
        for r in PACK11["requirements"]:
            used |= set(r.get("direct_areas", [])) | set(r.get("transferable_areas", []))
        self.assertTrue(NEW_AREAS <= used, NEW_AREAS - used)


BRIDGES = json.loads((ROOT / "knowledge_packs/pm-bridges-v1.json").read_text(encoding="utf-8"))
ACTION_LIB = json.loads((ROOT / "knowledge_packs/pm-actions-v1.json").read_text(encoding="utf-8"))
ELIG = json.loads((ROOT / "knowledge_packs/pm-eligibility-v1.json").read_text(encoding="utf-8"))


class TransitionContent(unittest.TestCase):
    """Phase 4 content (D27-D29): generated from reviewed JSON; the approved rules hold."""

    def test_seed_migration_matches_the_reviewed_json(self):
        r = subprocess.run([sys.executable, "scripts/build_transition_content.py", "--check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bridge_catalogue_is_small_sourced_and_has_no_program_manager(self):
        pairs = sorted((b["target_role_id"], b["bridge_role_id"]) for b in BRIDGES["bridges"])
        self.assertEqual(pairs, [("PM-PM", "PM-APM"), ("PM-PM", "ROLE-OWNER"), ("PM-PM", "ROLE-PAN"), ("PM-SPM", "PM-PM")])
        for b in BRIDGES["bridges"]:
            for k in ("responsibilities", "develops", "entry_requirements_stated", "suitable_backgrounds", "suitability_note",
                      "tradeoff_note", "source_keys"):
                self.assertTrue(b[k], (b["bridge_role_id"], k))
        self.assertEqual([x["role_id"] for x in BRIDGES["not_bridges"]], ["ROLE-PGM"])
        for s in BRIDGES["sources"]:
            self.assertTrue(s["licence"] and s["attribution"] and s["url"].startswith("https://") and s["last_verified_at"])

    def test_every_employer_criterion_is_structured_exactly_once(self):
        stated = {(s["key"], c["text"]) for s in PACK["sources"] for c in s.get("employer_criteria", [])}
        covered = [(c["source"], c["text"]) for c in ELIG["criteria"]]
        self.assertEqual(sorted(stated), sorted(covered))

    def test_no_title_only_eligibility_and_exclusions_apply_to_the_current_role(self):
        text = json.dumps(ELIG)
        self.assertNotIn("title", json.dumps([c["rule"] for c in ELIG["criteria"]]))
        j11 = next(c for c in ELIG["criteria"] if c["source"] == "J11")["rule"]
        self.assertEqual(j11["any"][1]["all"][1]["type"], "product_exposure")
        j12 = next(c for c in ELIG["criteria"] if c["source"] == "J12")["rule"]
        self.assertIn({"type": "not_transitioning_from", "families": ["consulting", "project_program_management"]}, j12["all"])
        self.assertIn("transitioning now", text)

    def test_action_templates_cover_every_requirement_and_stage(self):
        keys = {t["key"] for t in ACTION_LIB["templates"]}
        for r in json.loads((ROOT / "knowledge_packs/pm-v1.1.json").read_text(encoding="utf-8"))["requirements"]:
            self.assertTrue({f"build.{r['key']}", f"demonstrate.{r['key']}"} <= keys, r["key"])
        self.assertEqual({t["stage"] for t in ACTION_LIB["templates"]}, {0, 1, 2, 3, 4})



PACK12 = json.loads((ROOT / "knowledge_packs/pm-v1.2.json").read_text(encoding="utf-8"))
BRIDGES2 = json.loads((ROOT / "knowledge_packs/pm-bridges-v2.json").read_text(encoding="utf-8"))
ACTIONS2 = json.loads((ROOT / "knowledge_packs/pm-actions-v2.json").read_text(encoding="utf-8"))
ELIG2 = json.loads((ROOT / "knowledge_packs/pm-eligibility-v2.json").read_text(encoding="utf-8"))


class PhaseFourRemediationContent(unittest.TestCase):
    """D31-D36 content: generated from reviewed JSON; earlier versions stay unchanged."""

    def test_pm_v1_2_decision_rights_and_c4(self):
        dr = sorted(r["key"] for r in PACK12["requirements"] if r.get("decision_rights"))
        self.assertEqual(dr, ["C2", "C4", "L1"])                                        # C1 excluded (D31)
        for r in PACK12["requirements"]:
            if r.get("decision_rights"):
                self.assertEqual(r["direct_skills"], [], r["key"])                        # a stated skill is not ownership
        c4 = next(r for r in PACK12["requirements"] if r["key"] == "C4")
        self.assertEqual(c4["area_conditions"]["data_analysis"], "outcome_metrics_influence")
        self.assertEqual(c4["area_condition_fallback"], {"data_analysis": "transferable"})
        old = {r["key"]: r for r in PACK11["requirements"]}
        for r in PACK12["requirements"]:
            self.assertEqual(r["evidence"], old[r["key"]]["evidence"], r["key"])          # same sources and stances

    def test_bridges_v2_partial_development_and_labels(self):
        b = {x["bridge_role_id"]: x for x in BRIDGES2["bridges"]}
        self.assertTrue({"C2", "C4"} <= set(b["PM-APM"]["partly_develops"]))
        self.assertFalse({"C2", "C4"} & set(b["PM-APM"]["develops"]))
        self.assertEqual((b["ROLE-OWNER"]["may_be_primary"], b["ROLE-OWNER"]["entry_evidence"], b["ROLE-OWNER"]["progression_evidence"]),
                         (False, "hypothesis", "hypothesis"))
        self.assertEqual(b["ROLE-PAN"]["progression_evidence"], "hypothesis")
        for x in BRIDGES2["bridges"]:
            self.assertEqual(set(x["partial_development"]), set(x["partly_develops"]), x["bridge_role_id"])
        pool = {c["f1_role"]: c for c in BRIDGES2["candidate_pool"]["roles"]}
        self.assertEqual(sorted(pool), ["Agile coach", "Business analyst", "Delivery manager", "Programme delivery manager", "Service owner"])
        self.assertIsNone(pool["Service owner"]["trace_role_family"])                    # not forced (D33)

    def test_actions_v2_labels_and_stage_four(self):
        t = {x["key"]: x for x in ACTIONS2["templates"]}
        for k, x in t.items():
            if x["kind"] == "build":
                self.assertIn("learning_exercise", x["labels"], k)
            if x["kind"] == "reposition":
                self.assertIn("factual_accuracy", x["labels"], k)
        for k in ("apply.check_criteria", "apply.case_study", "apply.alternative_routes", "apply.bridge_seek", "reposition.skill", "reposition.role"):
            self.assertIn(k, t)

    def test_eligibility_v2_covers_every_criterion_with_aliases(self):
        stated = sorted((s["key"], c["text"]) for s in PACK["sources"] for c in s.get("employer_criteria", []))
        self.assertEqual(sorted((c["source"], c["text"]) for c in ELIG2["criteria"]), stated)
        aliases = [a for f in ELIG2["education_fields"] for a in f["aliases"]]
        self.assertEqual(len(aliases), len(set(aliases)))


ELIG3 = json.loads((ROOT / "knowledge_packs/pm-eligibility-v3.json").read_text(encoding="utf-8"))


class EligibilityV3(unittest.TestCase):
    """Phase 4 closure: only J12's current-role leaf and J3's preference change; everything else equals v2."""

    def test_only_the_two_approved_criteria_change(self):
        v2 = {(c["source"], c["text"]): c for c in ELIG2["criteria"]}
        changed = sorted(k for k, c in ((k, c) for c in ELIG3["criteria"] for k in [(c["source"], c["text"])]) if c != v2[k])
        self.assertEqual([k[0] for k in changed], ["J12", "J3"])
        j12 = next(c for c in ELIG3["criteria"] if c["source"] == "J12")["rule"]["all"]
        self.assertEqual([x.get("unconfirmed_roles") for x in j12 if x["type"] == "current_role_in_families"], [["PM-APM"]])
        self.assertIn({"type": "not_transitioning_from", "families": ["consulting", "project_program_management"]}, j12)   # unchanged
        j3 = next(c for c in ELIG3["criteria"] if c["source"] == "J3" and c["text"].startswith("A computer science"))["rule"]
        self.assertEqual(j3["before_families"], ["product_management"])
        self.assertEqual(ELIG3["education_fields"], ELIG2["education_fields"])
