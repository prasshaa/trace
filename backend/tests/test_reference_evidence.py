"""Unit tests for episode-aware reference evidence retrieval, using an in-memory repository.

The fake repository stands in for the foundation's v2.1.0 episode objects so that empirical
scenarios (eligible, ineligible and withdrawn real profiles) can be tested without inserting
fabricated records into any database. It filters by mode the way trace_ref.v_mode_profiles does.
All data here is invented test data.
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.reference_evidence import (
    EvidenceIntegrityError,
    EvidenceRequest,
    InvalidEpisodeRoute,
    UnknownDestinationRole,
    UnsupportedEvidenceMode,
    UnsupportedFilter,
    retrieve_reference_evidence,
)
from app.services.reference_evidence.models import (
    AreaRow,
    EpisodePathRow,
    RoleRow,
    RouteObservationRow,
    RouteStep,
    RouteTimelineSummaryRow,
    StepExperienceRow,
)
from app.services.reference_evidence.repository import (
    EPISODE_ROUTE_OBSERVATIONS_SQL,
    EPISODE_ROUTE_SUMMARY_SQL,
    MODE_EPISODE_PATHS_SQL,
    ROLE_SQL,
)

ROLES = {
    "ROLE-SWE": "software_engineering", "ROLE-BA": "business_analysis", "PM-APM": "product_management",
    "PM-PM": "product_management", "PM-SPM": "product_management", "ROLE-QA": "qa_testing",
}
REAL, SYN = "consented_real_reference", "synthetic_test_fixture"
PM_SIGNALS = {"product_discovery", "roadmap_ownership", "prioritisation_backlog", "outcome_ownership"}
AREAS = PM_SIGNALS | {"technical_engineering", "data_analysis", "people_leadership"}


def M(y, m):
    return y * 12 + m - 1


def month(y, m):
    return (M(y, m), M(y, m))


def year(y):
    return (M(y, 1), M(y, 12))


def path(pid, cls, steps, starts, segment_no=1):
    """steps: [(role_id, entry_change_type)], the first entry change type is None."""
    return EpisodePathRow(profile_id=pid, dataset_class=cls, segment_no=segment_no,
                          role_path=[r for r, _ in steps], entry_change_types=[c for _, c in steps],
                          stint_start_lo=[s[0] for s in starts], stint_start_hi=[s[1] for s in starts])


class FakeRepo:
    """Emulates the foundation's episode objects: routes are matched without collapsing, and
    paths and route functions are restricted to the profiles that the mode may read
    (empirical = currently `eligible` real profiles, synthetic_test = synthetic class)."""

    def __init__(self, paths, eligible=(), summary_overrides=None, evidence=None, threshold=5.0):
        self.paths = paths
        self.eligible = set(eligible)
        self.summary_overrides = summary_overrides or {}
        # evidence: {(profile_id, segment_no, stint_no): [responsibility areas, possibly repeated]}; emulates
        # experience_evidence joined through v_career_episode_stints.
        self.evidence = evidence or {}
        self.threshold = threshold
        self.calls = []

    def _in_mode(self, p, mode):
        if mode == "empirical":
            return p.dataset_class == REAL and p.profile_id in self.eligible
        return p.dataset_class == SYN

    def get_roles(self, role_ids):
        return {r: RoleRow(role_id=r, canonical_title=r, role_family=ROLES[r], is_pm_target=r.startswith("PM-"),
                           is_resolved=True, active=True) for r in role_ids if r in ROLES}

    def mode_episode_paths_containing(self, role_id, mode):
        self.calls.append(("paths", mode))
        return [p for p in self.paths if self._in_mode(p, mode) and role_id in p.role_path]

    def _observations(self, route, mode):
        if len(route) < 2 or any(r not in ROLES for r in route):
            raise InvalidEpisodeRoute(f"invalid route {route}")
        out, n = {}, len(route)
        for p in sorted(self.paths, key=lambda p: (p.profile_id, p.segment_no)):
            if not self._in_mode(p, mode) or p.profile_id in out:
                continue
            for i in range(len(p.role_path) - n + 1):
                if tuple(p.role_path[i:i + n]) == tuple(route):
                    a, b = i, i + n - 1
                    out[p.profile_id] = RouteObservationRow(
                        profile_id=p.profile_id, segment_no=p.segment_no, start_position=i + 1,
                        step_change_types=p.entry_change_types[i + 1:i + n],
                        route_months_min=max(0, p.stint_start_lo[b] - p.stint_start_hi[a]),
                        route_months_max=p.stint_start_hi[b] - p.stint_start_lo[a],
                        is_precise=p.stint_start_lo[a] == p.stint_start_hi[a] and p.stint_start_lo[b] == p.stint_start_hi[b])
                    break
        return list(out.values())

    def episode_route_observations(self, route, mode):
        self.calls.append(("observations", mode))
        return self._observations(route, mode)

    def route_step_experience(self, requests, mode):
        self.calls.append(("step_experience", mode))
        out = set()
        for q in requests:
            rows = [p for p in self.paths if p.profile_id == q.profile_id and p.segment_no == q.segment_no and self._in_mode(p, mode)]
            if not rows:
                continue
            for stint in range(q.first_stint, q.last_stint + 1):
                for area in self.evidence.get((q.profile_id, q.segment_no, stint), []):
                    out.add((q.profile_id, area))
        return [StepExperienceRow(profile_id=a, responsibility_area=b) for a, b in sorted(out)]

    def responsibility_areas(self, areas):
        return {a: AreaRow(responsibility_area=a, label=a.replace("_", " ").title(), is_pm_ownership_signal=a in PM_SIGNALS)
                for a in areas if a in AREAS}

    def min_display_count(self):
        return self.threshold

    def episode_route_timeline_summary(self, route, mode):
        self.calls.append(("summary", mode))
        obs = self._observations(route, mode)
        base = dict(data_mode=mode, route=list(route), supporting_profile_count=len(obs),
                    dated_profile_count=sum(o.is_precise for o in obs), count_display_allowed=len(obs) >= 5,
                    evidence_class="synthetic_test_only" if mode != "empirical" else "insufficient",
                    p25_months=None, median_months=None, p75_months=None,
                    coarse_min_months=min((o.route_months_min for o in obs), default=None),
                    coarse_max_months=max((o.route_months_max for o in obs), default=None),
                    timeline_metric="route_start_to_target_start_months", timeline_display_allowed=False)
        base.update(self.summary_overrides.get(tuple(route), {}))
        return RouteTimelineSummaryRow(**base)


def run(repo, role="PM-PM", mode="empirical", **kw):
    return retrieve_reference_evidence(EvidenceRequest(destination_role_id=role, data_mode=mode, **kw), repo)


def mover(pid="REF-001", cls=REAL, start=(2018, 1), move=(2021, 7)):
    return path(pid, cls, [("PM-PM", None), ("PM-PM", "employer_change")], [month(*start), month(*move)])


# --------------------------------------------------------------------------- request handling
class TestRequest:
    def test_data_mode_is_required(self):
        with pytest.raises(ValidationError):
            EvidenceRequest(destination_role_id="PM-PM")

    def test_mode_must_be_a_foundation_value(self):
        with pytest.raises(ValidationError):
            EvidenceRequest(destination_role_id="PM-PM", data_mode="real")

    def test_synthetic_demo_is_not_enabled(self):
        with pytest.raises(UnsupportedEvidenceMode):
            run(FakeRepo([]), mode="synthetic_demo")

    def test_unknown_destination_role_is_rejected(self):
        with pytest.raises(UnknownDestinationRole):
            run(FakeRepo([]), role="PM-NOPE")

    def test_destination_company_is_rejected_not_ignored(self):
        repo = FakeRepo([mover()], eligible={"REF-001"})
        with pytest.raises(UnsupportedFilter):
            run(repo, destination_company="Acme")
        assert repo.calls == []


# --------------------------------------------------------------------------- episode scenarios
class TestEpisodeScenarios:
    def test_same_role_employer_change_is_a_pathway(self):
        r = run(FakeRepo([mover()], eligible={"REF-001"}))
        (o,) = r.observations
        assert o.route == ["PM-PM", "PM-PM"]
        assert o.step_change_types == ["employer_change"]
        assert [s.entry_change_type for s in o.steps] == [None, "employer_change"]
        assert (o.route_months_min, o.is_precise) == (42, True)
        assert [s.route for s in r.route_summaries] == [["PM-PM", "PM-PM"]]

    def test_role_change_within_same_employer(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-APM", None), ("PM-PM", "role_change")], [month(2019, 1), month(2021, 7)])],
                        eligible={"REF-001"})
        (o,) = run(repo).observations
        assert (o.route, o.step_change_types) == (["PM-APM", "PM-PM"], ["role_change"])

    def test_role_and_employer_change(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-PM", None), ("PM-SPM", "role_and_employer_change")],
                              [month(2018, 1), month(2021, 7)])], eligible={"REF-001"})
        (o,) = run(repo, role="PM-SPM").observations
        assert (o.route, o.step_change_types) == (["PM-PM", "PM-SPM"], ["role_and_employer_change"])

    def test_multiple_same_role_moves_use_the_first_arrival(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-PM", None), ("PM-PM", "employer_change"), ("PM-PM", "employer_change")],
                              [month(2016, 1), month(2019, 1), month(2022, 1)])], eligible={"REF-001"})
        (o,) = run(repo).observations
        assert o.route == ["PM-PM", "PM-PM"] and o.provenance.segment_no == 1
        assert (o.route_months_min, o.route_months_max) == (36, 36)

    def test_repeated_role_after_a_role_change(self):
        repo = FakeRepo([path("REF-001", REAL, [("ROLE-SWE", None), ("PM-APM", "role_and_employer_change"),
                                                 ("PM-APM", "employer_change"), ("PM-PM", "role_change")],
                              [month(2014, 1), month(2017, 1), month(2019, 1), month(2021, 1)])], eligible={"REF-001"})
        (o,) = run(repo).observations
        assert o.route == ["ROLE-SWE", "PM-APM", "PM-APM", "PM-PM"]
        assert o.step_change_types == ["role_and_employer_change", "employer_change", "role_change"]

    def test_single_episode_is_not_a_pathway(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-PM", None)], [month(2019, 1)])], eligible={"REF-001"})
        r = run(repo)
        assert (r.has_observed_pathways, r.observations, r.route_summaries) == (False, [], [])

    def test_destination_only_at_segment_start_in_every_segment_is_not_a_pathway(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-PM", None)], [month(2016, 1)], segment_no=1),
                         path("REF-001", REAL, [("PM-PM", None)], [month(2021, 9)], segment_no=2)], eligible={"REF-001"})
        assert run(repo).observations == []

    def test_first_route_in_history_is_used_once_per_individual(self):
        repo = FakeRepo([
            path("REF-001", REAL, [("PM-PM", None)], [month(2014, 1)], segment_no=1),
            path("REF-001", REAL, [("ROLE-QA", None), ("PM-PM", "role_and_employer_change"), ("PM-PM", "employer_change")],
                 [month(2015, 1), month(2017, 1), month(2019, 1)], segment_no=3),
        ], eligible={"REF-001"})
        r = run(repo)
        assert [o.route for o in r.observations] == [["ROLE-QA", "PM-PM"]]
        assert r.observations[0].provenance.segment_no == 3

    def test_exact_destination_role_matching(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-APM", None), ("PM-SPM", "role_change")], [month(2018, 1), month(2021, 1)]),
                         path("REF-002", REAL, [("PM-APM", None), ("PM-PM", "role_change")], [month(2018, 1), month(2020, 1)])],
                        eligible={"REF-001", "REF-002"})
        assert [o.route for o in run(repo).observations] == [["PM-APM", "PM-PM"]]

    def test_multiple_observations_grouped_by_route(self):
        repo = FakeRepo([
            mover("REF-001"), mover("REF-002", start=(2017, 1), move=(2019, 7)),
            path("REF-003", REAL, [("ROLE-BA", None), ("PM-PM", "role_and_employer_change")], [month(2015, 1), month(2018, 1)]),
        ], eligible={"REF-001", "REF-002", "REF-003"})
        r = run(repo)
        assert sorted(o.route for o in r.observations) == [["PM-PM", "PM-PM"], ["PM-PM", "PM-PM"], ["ROLE-BA", "PM-PM"]]
        assert {tuple(s.route): s.supporting_profile_count for s in r.route_summaries} == {
            ("PM-PM", "PM-PM"): 2, ("ROLE-BA", "PM-PM"): 1}

    def test_single_observation_is_returned_without_any_minimum(self):
        r = run(FakeRepo([mover()], eligible={"REF-001"}))
        assert len(r.observations) == 1
        assert r.route_summaries[0].count_display_allowed is False  # foundation flag passed through


# --------------------------------------------------------------------------- modes and eligibility
class TestModes:
    def mixed(self):
        return FakeRepo([mover("REF-001"), mover("REF-002"), mover("SYN-001", SYN)], eligible={"REF-001"})

    def test_empirical_excludes_synthetic_and_ineligible_profiles(self):
        r = run(self.mixed(), mode="empirical")
        assert [o.provenance.profile_id for o in r.observations] == ["REF-001"]
        assert r.route_summaries[0].supporting_profile_count == 1
        assert r.is_synthetic_test_data is False

    def test_synthetic_mode_returns_only_synthetic_and_is_labelled(self):
        r = run(self.mixed(), mode="synthetic_test")
        assert [o.provenance.dataset_class for o in r.observations] == [SYN]
        assert r.is_synthetic_test_data and "SYNTHETIC TEST DATA" in r.evidence_notice
        assert all(s.evidence_class == "synthetic_test_only" and s.data_mode == "synthetic_test" for s in r.route_summaries)

    def test_empty_empirical_does_not_fall_back_to_synthetic(self):
        repo = FakeRepo([mover("SYN-001", SYN)])
        r = run(repo, mode="empirical")
        assert r.observations == [] and r.data_mode == "empirical" and not r.is_synthetic_test_data
        assert {mode for _, mode in repo.calls} == {"empirical"}

    def test_every_query_uses_only_the_requested_mode(self):
        repo = self.mixed()
        run(repo, mode="synthetic_test")
        assert {mode for _, mode in repo.calls} == {"synthetic_test"}

    def test_consent_withdrawal_applies_to_the_next_retrieval(self):
        repo = FakeRepo([mover("REF-001")], eligible={"REF-001"})
        assert len(run(repo).observations) == 1
        repo.eligible.discard("REF-001")  # the contributor withdraws; v_eligible_profiles drops them
        r = run(repo)
        assert (r.observations, r.route_summaries) == ([], [])

    def test_wrong_dataset_class_from_the_source_is_refused(self):
        class Leaky(FakeRepo):
            def mode_episode_paths_containing(self, role_id, mode):
                return [p for p in self.paths if role_id in p.role_path]  # forgets the mode join
        with pytest.raises(EvidenceIntegrityError):
            run(Leaky(self.mixed().paths, eligible={"REF-001"}), mode="empirical")

    def test_summary_from_another_mode_is_refused(self):
        repo = FakeRepo([mover()], eligible={"REF-001"},
                        summary_overrides={("PM-PM", "PM-PM"): {"evidence_class": "synthetic_test_only"}})
        with pytest.raises(EvidenceIntegrityError):
            run(repo)

    def test_observation_disagreeing_with_paths_is_refused(self):
        class Disagreeing(FakeRepo):
            def episode_route_observations(self, route, mode):
                return []
        with pytest.raises(EvidenceIntegrityError):
            run(Disagreeing([mover()], eligible={"REF-001"}))

    def test_change_types_disagreeing_with_paths_are_refused(self):
        class WrongChanges(FakeRepo):
            def episode_route_observations(self, route, mode):
                return [o.model_copy(update={"step_change_types": ["role_change"]}) for o in self._observations(route, mode)]
        with pytest.raises(EvidenceIntegrityError):
            run(WrongChanges([mover()], eligible={"REF-001"}))

    def test_foundation_route_rejection_is_not_reported_as_empty_evidence(self):
        class Rejecting(FakeRepo):
            def episode_route_timeline_summary(self, route, mode):
                raise InvalidEpisodeRoute("rejected")
        with pytest.raises(EvidenceIntegrityError):
            run(Rejecting([mover()], eligible={"REF-001"}))


# --------------------------------------------------------------------------- labels, precision, timelines
class TestPassThrough:
    def test_evidence_labels_and_percentiles_are_passed_through_unchanged(self):
        override = {"evidence_class": "moderate", "supporting_profile_count": 22, "dated_profile_count": 21,
                    "count_display_allowed": True, "p25_months": Decimal("20.5"), "median_months": Decimal("30"),
                    "p75_months": Decimal("41.25"), "timeline_display_allowed": True}
        repo = FakeRepo([mover()], eligible={"REF-001"}, summary_overrides={("PM-PM", "PM-PM"): override})
        s = run(repo).route_summaries[0]
        for k, v in override.items():
            assert getattr(s, k) == v

    def test_year_precision_is_preserved_not_rounded(self):
        repo = FakeRepo([path("REF-001", REAL, [("PM-PM", None), ("PM-PM", "employer_change")], [year(2015), month(2019, 4)])],
                        eligible={"REF-001"})
        o = run(repo).observations[0]
        assert o.is_precise is False
        assert (o.steps[0].start_is_month_precise, o.steps[0].start_lo_label, o.steps[0].start_hi_label) == (False, "2015-01", "2015-12")
        assert o.steps[1].start_is_month_precise is True
        assert (o.route_months_min, o.route_months_max) == (40, 51)

    def test_insufficient_timeline_data_stays_null(self):
        s = run(FakeRepo([mover()], eligible={"REF-001"})).route_summaries[0]
        assert (s.p25_months, s.median_months, s.p75_months, s.timeline_display_allowed) == (None, None, None, False)
        assert s.timeline_metric == "route_start_to_target_start_months"

    def test_frequency_is_never_described_as_probability(self):
        assert "not a probability" in run(FakeRepo([])).interpretation_note


# --------------------------------------------------------------------------- privacy
class TestPrivacy:
    def test_serialised_result_has_no_profile_identifiers(self):
        r = run(FakeRepo([mover()], eligible={"REF-001"}))
        dumped = r.model_dump_json()
        assert "REF-001" not in dumped and "provenance" not in dumped and "profile_id" not in dumped
        assert r.observations[0].provenance.profile_id == "REF-001"  # still available internally
        assert "REF-001" not in repr(r.observations[0])

    def test_no_employer_identifier_anywhere_in_the_contract(self):
        for model in (EpisodePathRow, RouteObservationRow, RouteStep):
            assert not [f for f in model.model_fields if "employer" in f], model
        dumped = run(FakeRepo([mover()], eligible={"REF-001"})).model_dump_json()
        assert "-ORG-" not in dumped and "employer_id" not in dumped

    def test_queries_select_no_identifying_or_employer_columns(self):
        sql = " ".join([ROLE_SQL, MODE_EPISODE_PATHS_SQL, EPISODE_ROUTE_OBSERVATIONS_SQL, EPISODE_ROUTE_SUMMARY_SQL]).lower()
        for col in ("contributor_user_id", "source_consent_event_id", "email", "profile_summary", "title_raw",
                    "source_ref", "region_group", "employer_id"):
            assert col not in sql, col

    def test_only_episode_objects_are_queried(self):
        sql = " ".join([MODE_EPISODE_PATHS_SQL, EPISODE_ROUTE_OBSERVATIONS_SQL, EPISODE_ROUTE_SUMMARY_SQL])
        assert "trace_ref.v_career_paths" not in sql
        assert "trace_ref.route_observations(" not in sql and "trace_ref.route_timeline_summary(" not in sql
        assert "trace_ref.v_career_episode_paths" in sql
        assert "trace_ref.episode_route_observations(" in sql and "trace_ref.episode_route_timeline_summary(" in sql

    def test_paths_query_is_restricted_by_mode(self):
        sql = " ".join(MODE_EPISODE_PATHS_SQL.split())
        assert "join trace_ref.v_mode_profiles mp on mp.profile_id = cp.profile_id and mp.data_mode = %(data_mode)s" in sql


# --------------------------------------------------------------------------- origin role (M5)
class TestOrigin:
    def career(self, pid, steps, cls=SYN, segment_no=1):
        starts = [month(2015 + i, 1) for i in range(len(steps))]
        return path(pid, cls, steps, starts, segment_no=segment_no)

    def test_routes_start_at_the_origin_and_end_at_the_first_later_destination(self):
        p = self.career("SYN-A", [("ROLE-SWE", None), ("PM-APM", "role_change"), ("PM-PM", "role_change"), ("PM-SPM", "role_change")])
        r = run(FakeRepo([p]), role="PM-SPM", mode="synthetic_test", origin_role_id="PM-APM")
        (o,) = r.observations
        assert o.route == ["PM-APM", "PM-PM", "PM-SPM"] and o.step_change_types == ["role_change", "role_change"]
        assert o.steps[0].entry_change_type is None and [s.entry_change_type for s in o.steps[1:]] == ["role_change", "role_change"]
        assert [s.route for s in r.route_summaries] == [["PM-APM", "PM-PM", "PM-SPM"]]
        assert r.origin.role_id == "PM-APM" and r.destination.role_id == "PM-SPM" and r.is_synthetic_test_data
        assert any("origin role" in x for x in r.limitations)

    def test_people_who_never_held_the_origin_or_reached_it_after_the_destination_are_excluded(self):
        other = self.career("SYN-B", [("ROLE-BA", None), ("PM-SPM", "role_change")])
        later = self.career("SYN-C", [("PM-SPM", None), ("PM-APM", "role_change")])
        split = [self.career("SYN-D", [("PM-APM", None), ("ROLE-QA", "role_change")], segment_no=1),
                 self.career("SYN-D", [("PM-SPM", None), ("PM-PM", "role_change")], segment_no=2)]
        r = run(FakeRepo([other, later, *split]), role="PM-SPM", mode="synthetic_test", origin_role_id="PM-APM")
        assert (r.has_observed_pathways, r.observations, r.route_summaries) == (False, [], [])     # empty, honestly

    def test_origin_equal_to_destination_is_a_move_between_two_episodes(self):
        r = run(FakeRepo([mover("SYN-E", cls=SYN)]), role="PM-PM", mode="synthetic_test", origin_role_id="PM-PM")
        (o,) = r.observations
        assert o.route == ["PM-PM", "PM-PM"] and o.step_change_types == ["employer_change"]

    def test_first_origin_with_a_later_destination_in_the_lowest_segment(self):
        p1 = self.career("SYN-F", [("PM-APM", None), ("ROLE-QA", "role_change")], segment_no=1)
        p2 = self.career("SYN-F", [("PM-APM", None), ("PM-APM", "employer_change"), ("PM-PM", "role_change"), ("PM-PM", "employer_change")],
                         segment_no=2)
        (o,) = run(FakeRepo([p1, p2]), role="PM-PM", mode="synthetic_test", origin_role_id="PM-APM").observations
        assert o.route == ["PM-APM", "PM-APM", "PM-PM"] and o.provenance.segment_no == 2

    def test_unknown_origin_is_refused(self):
        from app.services.reference_evidence import UnknownOriginRole
        with pytest.raises(UnknownOriginRole):
            run(FakeRepo([]), mode="synthetic_test", origin_role_id="ROLE-NOPE")

    def test_without_origin_behaviour_is_unchanged(self):
        p = self.career("SYN-G", [("PM-APM", None), ("PM-PM", "role_change")])
        r = run(FakeRepo([p]), mode="synthetic_test")
        assert r.origin is None and r.observations[0].route == ["PM-APM", "PM-PM"]
        assert not any("origin role" in x for x in r.limitations)

    def test_disagreeing_start_position_is_an_integrity_error(self):
        p = self.career("SYN-H", [("ROLE-SWE", None), ("PM-APM", "role_change"), ("PM-PM", "role_change")])
        repo = FakeRepo([p])
        real = repo._observations
        repo._observations = lambda route, mode: [o.model_copy(update={"start_position": 1}) for o in real(route, mode)]
        with pytest.raises(EvidenceIntegrityError):
            run(repo, mode="synthetic_test", origin_role_id="PM-APM")


# --------------------------------------------------------------------------- route step experience (M6)
def cohort(n, areas_by_profile, cls=SYN, prefix="SYN"):
    """n synthetic APM -> PM observations; areas_by_profile(i) gives profile i's recorded areas on the APM step."""
    paths = [path(f"{prefix}-{i}", cls, [("PM-APM", None), ("PM-PM", "role_change")], [month(2018, 1), month(2020, 1)]) for i in range(n)]
    return paths, {(f"{prefix}-{i}", 1, 1): areas_by_profile(i) for i in range(n)}


def step(repo, **kw):
    (x,) = run(repo, mode=kw.pop("mode", "synthetic_test"), **kw).route_step_experience
    return x


class TestStepExperience:
    def apm_pm(self, pid, cls=SYN, segment_no=1):
        return path(pid, cls, [("PM-APM", None), ("PM-PM", "role_change")], [month(2018, 1), month(2020, 1)], segment_no=segment_no)

    # ---- privacy rules (threshold 5, the foundation default)
    def test_four_observations_suppress_every_area_detail(self):
        paths, ev = cohort(4, lambda i: ["roadmap_ownership", "outcome_ownership"])
        x = step(FakeRepo(paths, evidence=ev))
        assert (x.state, x.observation_count, x.areas, x.areas_withheld) == ("suppressed_small_cohort", None, [], False)
        assert "roadmap" not in x.model_dump_json() and "outcome" not in x.model_dump_json()

    def test_five_observations_and_an_area_recorded_by_two_profiles_withholds_that_area(self):
        paths, ev = cohort(5, lambda i: ["data_analysis"] if i < 2 else [])
        x = step(FakeRepo(paths, evidence=ev))
        assert (x.state, x.observation_count, x.areas, x.areas_withheld) == ("areas_withheld", 5, [], True)
        assert "data_analysis" not in x.model_dump_json()

    def test_five_observations_and_an_area_recorded_by_five_profiles_is_disclosed_and_common(self):
        paths, ev = cohort(5, lambda i: ["roadmap_ownership"] + (["data_analysis"] if i < 2 else []))
        x = step(FakeRepo(paths, evidence=ev))
        assert (x.state, x.observation_count, x.areas_withheld) == ("areas_disclosed", 5, True)
        assert [(a.responsibility_area, a.profile_count, a.is_common, a.is_pm_ownership_signal) for a in x.areas] == [
            ("roadmap_ownership", 5, True, True)]

    def test_ten_observations_and_an_area_recorded_by_five_profiles_is_disclosed_but_not_common(self):
        paths, ev = cohort(10, lambda i: ["roadmap_ownership"] if i < 5 else ["outcome_ownership"] + (["product_discovery"] if i == 9 else []))
        x = step(FakeRepo(paths, evidence=ev))
        assert (x.state, x.observation_count, x.areas_withheld) == ("areas_disclosed", 10, True)
        assert [(a.responsibility_area, a.profile_count, a.is_common) for a in x.areas] == [
            ("outcome_ownership", 5, False), ("roadmap_ownership", 5, False)]

    def test_suppressed_withheld_and_genuinely_empty_are_distinguishable(self):
        small, ev_small = cohort(4, lambda i: [])                               # nothing recorded, but too few to say so
        empty, ev_empty = cohort(5, lambda i: [])                               # genuinely empty
        rare, ev_rare = cohort(5, lambda i: ["data_analysis"] if i == 0 else [])
        states = [step(FakeRepo(p, evidence=e)).state for p, e in ((small, ev_small), (empty, ev_empty), (rare, ev_rare))]
        assert states == ["suppressed_small_cohort", "no_recorded_areas", "areas_withheld"]
        assert run(FakeRepo([]), mode="synthetic_test").route_step_experience == []   # no observed route at all

    def test_no_exact_count_below_five_and_no_indirect_disclosure(self):
        for n in range(1, 13):
            for k in range(0, n + 1):
                paths, ev = cohort(n, lambda i, k=k: (["roadmap_ownership"] if i < k else []) + ["outcome_ownership"])
                x = step(FakeRepo(paths, evidence=ev))
                d = x.model_dump()
                if n < 5:
                    assert d["observation_count"] is None and d["areas"] == [] and d["areas_withheld"] is False, (n, k)
                    assert d["state"] == "suppressed_small_cohort"
                else:
                    assert d["observation_count"] == n
                    assert all(a["profile_count"] >= 5 for a in d["areas"]), (n, k)
                    listed = {a["responsibility_area"] for a in d["areas"]}
                    assert ("roadmap_ownership" in listed) == (k >= 5), (n, k)
                    assert all(a["is_common"] == (a["profile_count"] * 2 > n) for a in d["areas"])
                assert set(d) == {"level", "data_mode", "route", "pre_destination_steps", "state", "observation_count", "areas_withheld", "areas"}

    def test_a_missing_threshold_discloses_nothing(self):
        paths, ev = cohort(8, lambda i: ["roadmap_ownership"])
        x = step(FakeRepo(paths, evidence=ev, threshold=None))
        assert (x.state, x.observation_count, x.areas) == ("suppressed_small_cohort", None, [])

    # ---- aggregation (threshold 1, so that small fixtures are visible)
    def test_several_areas_and_rows_on_one_profile_do_not_inflate_the_observation_count(self):
        repo = FakeRepo([self.apm_pm("SYN-1")], threshold=1,
                        evidence={("SYN-1", 1, 1): ["roadmap_ownership", "roadmap_ownership", "outcome_ownership", "data_analysis"]})
        x = step(repo)
        assert (x.route, x.pre_destination_steps, x.observation_count) == (["PM-APM", "PM-PM"], ["PM-APM"], 1)
        assert [(a.responsibility_area, a.profile_count) for a in x.areas] == [
            ("data_analysis", 1), ("outcome_ownership", 1), ("roadmap_ownership", 1)]

    def test_an_area_counts_once_per_distinct_profile(self):
        p1 = path("SYN-1", SYN, [("ROLE-SWE", None), ("PM-APM", "role_change"), ("PM-PM", "role_change")],
                  [month(2016, 1), month(2018, 1), month(2020, 1)])
        p2 = path("SYN-2", SYN, [("ROLE-SWE", None), ("PM-APM", "role_change"), ("PM-PM", "role_change")],
                  [month(2016, 1), month(2018, 1), month(2020, 1)])
        repo = FakeRepo([p1, p2], threshold=1, evidence={("SYN-1", 1, 1): ["data_analysis"], ("SYN-1", 1, 2): ["data_analysis"],
                                                         ("SYN-2", 1, 2): ["data_analysis", "roadmap_ownership"]})
        x = step(repo)
        assert {a.responsibility_area: a.profile_count for a in x.areas} == {"data_analysis": 2, "roadmap_ownership": 1}
        assert x.observation_count == 2

    def test_the_service_counts_profiles_even_if_the_repository_repeats_rows(self):
        repo = FakeRepo([self.apm_pm("SYN-1"), self.apm_pm("SYN-2")], threshold=1,
                        evidence={("SYN-1", 1, 1): ["roadmap_ownership"], ("SYN-2", 1, 1): ["roadmap_ownership"]})
        real = repo.route_step_experience
        repo.route_step_experience = lambda q, m: real(q, m) * 3
        x = step(repo)
        assert (x.observation_count, x.areas[0].profile_count) == (2, 2)

    def test_only_steps_before_the_destination_contribute(self):
        repo = FakeRepo([self.apm_pm("SYN-1")], threshold=1,
                        evidence={("SYN-1", 1, 1): ["prioritisation_backlog"], ("SYN-1", 1, 2): ["people_leadership"]})
        assert [a.responsibility_area for a in step(repo).areas] == ["prioritisation_backlog"]

    def test_with_an_origin_steps_before_the_origin_do_not_contribute_either(self):
        p = path("SYN-1", SYN, [("ROLE-SWE", None), ("PM-APM", "role_change"), ("PM-PM", "role_change"), ("PM-SPM", "role_change")],
                 [month(2015, 1), month(2017, 1), month(2019, 1), month(2021, 1)])
        repo = FakeRepo([p], threshold=1, evidence={("SYN-1", 1, 1): ["technical_engineering"], ("SYN-1", 1, 2): ["product_discovery"],
                                                    ("SYN-1", 1, 3): ["roadmap_ownership"], ("SYN-1", 1, 4): ["people_leadership"]})
        x = step(repo, role="PM-SPM", origin_role_id="PM-APM")
        assert x.pre_destination_steps == ["PM-APM", "PM-PM"]
        assert [a.responsibility_area for a in x.areas] == ["product_discovery", "roadmap_ownership"]

    def test_synthetic_and_empirical_evidence_stay_isolated(self):
        paths = [self.apm_pm("REF-1", cls=REAL), self.apm_pm("REF-2", cls=REAL), self.apm_pm("SYN-1")]
        ev = {("REF-1", 1, 1): ["roadmap_ownership"], ("REF-2", 1, 1): ["data_analysis"], ("SYN-1", 1, 1): ["outcome_ownership"]}
        emp = run(FakeRepo(paths, eligible={"REF-1"}, evidence=ev, threshold=1), mode="empirical")
        syn = run(FakeRepo(paths, eligible={"REF-1"}, evidence=ev, threshold=1), mode="synthetic_test")
        assert [a.responsibility_area for x in emp.route_step_experience for a in x.areas] == ["roadmap_ownership"]
        assert [a.responsibility_area for x in syn.route_step_experience for a in x.areas] == ["outcome_ownership"]
        assert {x.data_mode for x in emp.route_step_experience} == {"empirical"} and syn.is_synthetic_test_data
        assert {x.data_mode for x in syn.route_step_experience} == {"synthetic_test"}

    def test_no_reference_identifiers_reach_the_client(self):
        r = run(FakeRepo([self.apm_pm("SYN-SECRET-1")], threshold=1, evidence={("SYN-SECRET-1", 1, 1): ["roadmap_ownership"]}),
                mode="synthetic_test")
        dumped = r.model_dump_json()
        assert "SYN-SECRET-1" not in dumped and "profile_id" not in dumped and "segment_no" not in dumped
        assert "SYN-SECRET-1" not in repr(r.route_step_experience)

    def test_areas_are_ordered_by_code_not_by_count(self):
        paths, ev = cohort(9, lambda i: ["roadmap_ownership"] + (["data_analysis"] if i < 5 else []))
        assert [a.responsibility_area for a in step(FakeRepo(paths, evidence=ev)).areas] == ["data_analysis", "roadmap_ownership"]

    def test_foreign_profiles_and_unknown_areas_are_integrity_errors(self):
        repo = FakeRepo([self.apm_pm("SYN-1")], threshold=1, evidence={("SYN-1", 1, 1): ["roadmap_ownership"]})
        real = repo.route_step_experience
        repo.route_step_experience = lambda q, m: real(q, m) + [StepExperienceRow(profile_id="SYN-OTHER", responsibility_area="data_analysis")]
        with pytest.raises(EvidenceIntegrityError):
            run(repo, mode="synthetic_test")
        with pytest.raises(EvidenceIntegrityError):
            run(FakeRepo([self.apm_pm("SYN-1")], threshold=1, evidence={("SYN-1", 1, 1): ["not_an_area"]}), mode="synthetic_test")
