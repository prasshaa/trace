"""Guardrails, parser wraps, sharing copy, and the demo narrative contract. No database and no network."""
import json

import pytest

from app.api.ratelimit import client_ip
from app.config import Settings
from app.services.ai_assessment.client import LlmError, OpenAICompatible, _json_object, client_from_settings
from app.services.ai_assessment.guardrails import apply_guardrails, collaboration_as_ownership, invented_number, negation_as_positive
from app.services.capability.interpret import interpret
from app.services.resume_import import DateInput, PlannedEducation, education_contract
from app.services.resume_parsing.document import Document, Line
from app.services.resume_parsing.parser import experience, parse_document
from app.services.transition.service import support_depth
from evaluation.ai_demo.personas import PERSONAS, lines_for


class _Req:
    def __init__(self, headers):
        self.headers = headers
        self.client = type("C", (), {"host": "10.0.0.8"})()


def test_wrapped_bullet_keeps_the_figure_and_the_negation():
    rows = [
        Line(text="Software Engineer | Northwind Labs | Jan 2021 – Present", index=0, x0=72, x1=460, top=80, bottom=92,
             col_left=72, col_right=540),
        Line(text="Raised onboarding completion from", bullet=True, index=1, x0=72, x1=400, top=100, bottom=112,
             col_left=72, col_right=540),
        Line(text="91% to 96%. Did not own the roadmap.", index=2, x0=90, x1=420, top=116, bottom=128,
             col_left=72, col_right=540),
        Line(text="Product Manager | Lumen Health | Feb 2023 – Present", index=3, x0=72, x1=460, top=150, bottom=162,
             col_left=72, col_right=540),
        Line(text="Owned the claims roadmap.", bullet=True, index=4, x0=72, x1=300, top=170, bottom=182,
             col_left=72, col_right=540),
    ]
    jobs = experience(rows)
    assert [j.title for j in jobs] == ["Software Engineer", "Product Manager"]
    first = " ".join(b.text for b in jobs[0].bullets) + " " + (jobs[0].description or "")
    assert "91% to 96%" in first and "did not own the roadmap" in first.lower()
    assert "91%" not in " ".join(b.text for b in jobs[1].bullets)


def test_a_collaboration_sentence_does_not_become_the_next_role():
    rows = [
        Line(text="QA Engineer | Brightline | Mar 2019 – Dec 2021", index=0, x0=72, x1=400, top=40, bottom=52),
        Line(text="Wrote regression suites.", bullet=True, index=1, x0=72, x1=300, top=60, bottom=72),
        Line(text="Partnered with the Product Manager on acceptance criteria.", index=2, x0=72, x1=460, top=80, bottom=92),
        Line(text="Filed the weekly defect note.", bullet=True, index=3, x0=72, x1=320, top=100, bottom=112),
    ]
    jobs = experience(rows)
    assert len(jobs) == 1
    blob = " ".join(b.text for b in jobs[0].bullets) + " " + (jobs[0].description or "")
    assert "Partnered with the Product Manager" in blob
    assert "Filed the weekly defect note." in blob


def test_year_only_education_is_accepted():
    item = PlannedEducation(key="D1", qualification="B.Tech", institution="Kestrel Valley Institute",
                            field_of_study="Computer Science",
                            start=DateInput(year=2016, precision="year"), end=DateInput(year=2020, precision="year"),
                            studying=False)
    assert education_contract([item]) == []


def test_collaboration_is_not_ownership_and_counts_are_not_outcomes():
    worked = interpret("Worked with the product manager on the roadmap.")
    partnered = interpret("Partnered with design on the product roadmap.")
    supported = interpret("Supported the product roadmap.")
    merchants = interpret("Supported 12 merchants during the pilot.")
    negated = interpret("Didn’t own the roadmap.")
    assert "roadmap_ownership" not in worked.clear
    assert "roadmap_ownership" not in partnered.clear
    assert "roadmap_ownership" not in supported.clear
    assert "roadmap_ownership" not in supported.partial
    assert "outcome_ownership" not in merchants.clear and "outcome_ownership" not in merchants.partial
    assert "roadmap_ownership" not in negated.clear and "roadmap_ownership" not in negated.partial


def test_a_later_denial_removes_the_roadmap_claim():
    reading = interpret("Built the product roadmap for the team but did not own the roadmap.")
    assert "roadmap_ownership" not in reading.clear


def test_guardrails_drop_invented_numbers_negation_and_unowned_collaboration():
    lines = [{"id": "L1", "text": "Worked with the product manager on the roadmap. Did not own the roadmap.", "job": "SWE, Northwind"},
             {"id": "L2", "text": "Raised onboarding completion from 91% to 96%.", "job": "SWE, Northwind"}]
    raw = {"opening": "They improved completion by 40%.",
           "proves": [
               {"text": "They owned the roadmap.", "evidence_ids": ["L1"], "why": "the line says so"},
               {"text": "Onboarding completion moved from 91% to 96%.", "evidence_ids": ["L2"], "why": "the line states both figures"},
               {"text": "They reached 99% completion.", "evidence_ids": ["L2"], "why": "guess"},
               {"text": "A claim with no line.", "evidence_ids": [], "why": ""},
           ],
           "missing": [{"text": "No evidence of coaching another product manager.", "why": "the lines do not mention it"},
                       {"text": "They missed a 50% target.", "why": "invented"}],
           "questions": [{"text": "Did you choose the order of that work?", "evidence_id": "L1"},
                         {"text": "What about the secret 12%?", "evidence_id": "nope"}],
           "steps": [{"text": "Write up the onboarding change from 91% to 96% in your own words.", "evidence_ids": ["L2"], "why": "it is already in the line"}]}
    out = apply_guardrails(raw, lines)
    assert out["opening"] == ""
    assert [p["evidence_ids"] for p in out["proves"]] == [["L2"]]
    assert "50%" not in json.dumps(out["missing"])
    assert out["questions"] == [{"text": "Did you choose the order of that work?", "evidence_id": "L1"}]
    assert out["steps"][0]["evidence_ids"] == ["L2"]
    assert collaboration_as_ownership("They owned the roadmap.", lines[0]["text"])
    assert negation_as_positive("They owned the roadmap.", lines[0]["text"])
    assert invented_number("They reached 99%.", lines[1]["text"])


def test_support_depth_matches_the_three_moves_and_limits_the_rest():
    assert support_depth("ROLE-SWE", "PM-APM") == "deep"
    assert support_depth("ROLE-QA", "PM-PM") == "deep"
    assert support_depth("PM-APM", "PM-PM") == "deep"
    assert support_depth("PM-PM", "PM-SPM") == "deep"
    assert support_depth("PM-PM", "PM-PM") == "limited"
    assert support_depth("PM-PM", "PM-APM") == "limited"
    assert support_depth("ROLE-SWE", "PM-SPM") == "limited"
    assert support_depth("PM-APM", "PM-SPM") == "limited"


def test_client_ip_uses_fly_or_the_appended_hop_and_never_the_leftmost():
    assert client_ip(_Req({"fly-client-ip": "203.0.113.9", "x-forwarded-for": "1.2.3.4"})) == "203.0.113.9"
    assert client_ip(_Req({"x-forwarded-for": "1.2.3.4, 198.51.100.20"})) == "10.0.0.8"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("TRACE_TRUST_APPENDED_FORWARDED", "1")
    try:
        assert client_ip(_Req({"x-forwarded-for": "1.2.3.4, 198.51.100.20"})) == "198.51.100.20"
    finally:
        monkey.undo()


def test_the_model_client_is_absent_without_a_key_and_json_fences_parse():
    assert client_from_settings(Settings(trace_ai_assessment=False)) is None
    assert client_from_settings(Settings(trace_ai_assessment=True, trace_llm_model="demo-model")) is None
    assert _json_object('```json\n{"opening": "ok"}\n```') == {"opening": "ok"}
    client = OpenAICompatible(provider="openai", base_url="https://example.invalid/v1", model="demo-model", api_key="secret-not-logged")
    assert client.timeout == 120
    assert "secret-not-logged" not in repr(client)
    assert "secret-not-logged" not in client.base_url
    assert Settings().trace_llm_timeout == 120
    assert Settings().trace_llm_reasoning_effort == "low"
    assert client.reasoning_effort == "low"


def test_the_request_deadline_is_the_whole_call_and_a_5xx_is_retried_once(monkeypatch):
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request

    def body():
        return _json.dumps({"choices": [{"message": {"content": '{"opening": "ok"}'}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 4,
                                      "completion_tokens_details": {"reasoning_tokens": 1}}}).encode()

    class Resp:
        def __init__(self):
            self._buf = body()
            self.fp = None
        def read(self, _n=-1):
            data, self._buf = self._buf, b""
            return data
        def close(self):
            pass

    started = _time.monotonic()
    def hang(req, timeout=None, socks=None):
        _time.sleep(2)
        raise AssertionError("the caller should have stopped waiting")
    monkeypatch.setattr("app.services.ai_assessment.client._open", hang)
    slow = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7", api_key="secret-not-logged", timeout=0.35)
    with pytest.raises(LlmError, match="timed out"):
        slow.complete_json("system", "user")
    assert _time.monotonic() - started < 1.2
    assert slow.last_meta["attempts"] == 1
    assert "secret-not-logged" not in str(slow.last_meta)
    flaky_n = {"n": 0}
    def flaky(req, timeout=None, socks=None):
        flaky_n["n"] += 1
        if flaky_n["n"] == 1:
            raise TimeoutError("slow")
        return Resp()
    monkeypatch.setattr("app.services.ai_assessment.client._open", flaky)
    retry = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7", api_key="secret-not-logged", timeout=5)
    assert retry.complete_json("system", "user") == {"opening": "ok"}
    assert retry.last_meta["attempts"] == 2

    calls = {"n": 0, "effort": []}
    def once(req, timeout=None, socks=None):
        calls["n"] += 1
        calls["effort"].append(_json.loads(req.data.decode()).get("reasoning_effort"))
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return Resp()
    monkeypatch.setattr("app.services.ai_assessment.client._open", once)
    client = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7", api_key="secret-not-logged",
                              timeout=5, reasoning_effort="low")
    assert client.complete_json("system", "user") == {"opening": "ok"}
    assert client.last_meta["attempts"] == 2
    assert client.last_meta["usage"]["reasoning_tokens"] == 1
    assert calls["effort"] == ["low", "low"]
    blank = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7", api_key="secret-not-logged",
                             timeout=5, reasoning_effort="")
    calls["n"] = 1
    monkeypatch.setattr(
        "app.services.ai_assessment.client._open",
        lambda req, timeout=None, socks=None: (calls["effort"].append(_json.loads(req.data.decode()).get("reasoning_effort")) or Resp()),
    )
    blank.complete_json("system", "user")
    assert calls["effort"][-1] is None


def test_a_reset_connection_is_retried_once_inside_the_deadline(monkeypatch):
    import json as _json
    import urllib.error

    class Resp:
        def __init__(self):
            self._buf = _json.dumps({"choices": [{"message": {"content": '{"opening": "ok"}'}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
        def read(self, _n=-1):
            data, self._buf = self._buf, b""
            return data
        def close(self):
            pass

    calls = {"n": 0}
    slept = []
    def open_once(req, timeout=None, socks=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(ConnectionResetError("reset"))
        return Resp()
    monkeypatch.setattr("app.services.ai_assessment.client._open", open_once)
    monkeypatch.setattr("app.services.ai_assessment.client.time.sleep", lambda seconds: slept.append(seconds))
    client = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7",
                              api_key="secret-not-logged", timeout=5, first_attempt_seconds=5)
    assert client.complete_json("system", "user") == {"opening": "ok"}
    assert calls["n"] == 2 and client.last_meta["attempts"] == 2
    assert slept and 0 < slept[0] <= 0.4
    def refused(req, timeout=None, socks=None):
        raise PermissionError("not a connection")
    monkeypatch.setattr("app.services.ai_assessment.client._open", refused)
    other = OpenAICompatible(provider="xai", base_url="https://api.x.ai/v1", model="grok-4.7",
                             api_key="secret-not-logged", timeout=5)
    with pytest.raises(LlmError, match="could not be reached"):
        other.complete_json("system", "user")
    assert other.last_meta["attempts"] == 1


def test_headers_then_a_slow_body_cannot_outrun_the_deadline():
    """Headers at t=deadline-1, then a trickle. The call must return at the deadline, not when the body ends."""
    import socket
    import threading
    import time as _time

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(5)
    port = srv.getsockname()[1]
    trickled = {"n": 0}

    def handle(conn):
        try:
            conn.settimeout(2)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            _time.sleep(1.0)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 4000\r\nConnection: close\r\n\r\n"
            )
            for _ in range(400):
                conn.sendall(b"x" * 10)
                trickled["n"] += 10
                _time.sleep(0.02)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def accept():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        handle(conn)

    threading.Thread(target=accept, daemon=True).start()
    client = OpenAICompatible(
        provider="xai", base_url=f"http://127.0.0.1:{port}/v1", model="grok-4.7",
        api_key="secret-not-logged", timeout=2.0, first_attempt_seconds=2.0,
    )
    started = _time.monotonic()
    with pytest.raises(LlmError, match="timed out"):
        client.complete_json("system", "user")
    elapsed = _time.monotonic() - started
    srv.close()
    assert elapsed < 2.8
    assert trickled["n"] < 4000


def test_the_first_attempt_leaves_time_for_a_retry():
    import socket
    import threading
    import time as _time

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(5)
    port = srv.getsockname()[1]
    seen = {"n": 0}
    done = threading.Event()

    def handle(conn, which):
        try:
            conn.settimeout(2)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            if which == 1:
                _time.sleep(0.2)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5000\r\nConnection: close\r\n\r\n"
                )
                for _ in range(250):
                    conn.sendall(b"y" * 20)
                    _time.sleep(0.02)
            else:
                payload = b'{"choices":[{"message":{"content":"{\\"opening\\": \\"ok\\"}"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
                head = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                conn.sendall(head + payload)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def accept_loop():
        while not done.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            seen["n"] += 1
            threading.Thread(target=handle, args=(conn, seen["n"]), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    client = OpenAICompatible(
        provider="xai", base_url=f"http://127.0.0.1:{port}/v1", model="grok-4.7",
        api_key="secret-not-logged", timeout=4.0, first_attempt_seconds=1.0,
    )
    started = _time.monotonic()
    assert client.complete_json("system", "user") == {"opening": "ok"}
    elapsed = _time.monotonic() - started
    done.set()
    srv.close()
    assert client.last_meta["attempts"] == 2
    assert elapsed < 2.5
    assert seen["n"] >= 2


def test_round3_false_positives_and_the_length_cap():
    from app.services.ai_assessment.guardrails import keep_claim
    cited = "Worked with the client's product lead. I did not own the product after the project ended."
    kept, reason = keep_claim(
        {"text": "On your next client workshop, ask to own the problem statement and the success measure, while the product lead keeps the product.",
         "evidence_ids": ["F04-L2"], "why": "That is the next piece of the workshop you already run."},
        {"F04-L2": {"id": "F04-L2", "text": cited}}, require_citation=False, kind="step")
    assert kept and reason is None
    themes = "Supported the product team with the top ten ticket themes each month."
    out = apply_guardrails(
        {"questions": [{"text": "Which of the top ten themes hurt customers most?", "evidence_id": "F07-L2"}]},
        [{"id": "F07-L2", "text": themes}])
    assert out["questions"] and out["questions"][0]["evidence_id"] == "F07-L2"
    kept, reason = keep_claim(
        {"text": "Run a 30-minute session on the ticket themes you already share.", "evidence_ids": ["F07-L2"], "why": ""},
        {"F07-L2": {"id": "F07-L2", "text": themes}}, require_citation=False, kind="step")
    assert kept and reason is None, reason
    line = "Shipped the seller form and wrote the notes the squad used the next day."
    pad = " ".join(["detail"] * 35)
    many = {
        "opening": "You shipped the seller form. The gap is a written trade-off.",
        "strengths": [{"text": f"You shipped the seller form. {pad}", "evidence_ids": ["L1"], "why": pad} for _ in range(4)],
        "transferable": [{"text": f"This work carries into product discovery. {pad}", "evidence_ids": ["L1"], "why": pad} for _ in range(3)],
        "gaps": [{"text": f"You have not written the trade-off. {pad}", "why": pad} for _ in range(3)],
        "questions": [{"text": f"What did the squad do with the notes the next day? {pad}", "evidence_id": "L1"} for _ in range(3)],
        "steps": [{"text": f"Ask to draft the trade-off from the notes you already wrote. {pad}", "evidence_ids": ["L1"], "why": pad} for _ in range(3)],
    }
    capped = apply_guardrails(many, [{"id": "L1", "text": line}])
    assert capped["transferable"]
    assert len(capped["questions"]) < 3
    assert capped["dropped_reasons"].get("trimmed", 0) >= 1


def test_guardrail_probes_from_the_review():
    collab = "Worked with the product manager on the roadmap. Did not own the roadmap."
    metric = "Supported 12 merchants during a pilot. I did not set the success metric."
    onboard = "Raised onboarding completion from 91% to 96%."
    owned = "Built and shipped a scheduling tool and owned its short roadmap."
    lines = [{"id": "F01-L1", "text": collab}, {"id": "F01-L2", "text": metric}, {"id": "F01-L3", "text": onboard},
             {"id": "F08-L1", "text": owned}]
    by = {row["id"]: row for row in lines}

    def one(text, cited_id, kind="strength"):
        kept, reason = keep_claim({"text": text, "evidence_ids": [cited_id], "why": ""}, by, require_citation=True, kind=kind)
        return kept, reason

    from app.services.ai_assessment.guardrails import keep_claim
    for word in ("Owned the roadmap.", "Led the roadmap.", "Drove the roadmap.", "Ran the roadmap."):
        kept, reason = one(word, "F01-L1")
        assert kept is None and reason in {"negation", "collaboration_as_ownership"}
    for word in ("Managed the pilot and set its success metric.", "Set the pilot's success metric.", "Headed the merchant pilot."):
        kept, reason = one(word, "F01-L2")
        assert kept is None and reason in {"negation", "collaboration_as_ownership"}, word
    assert one("Doubled merchant retention in the pilot.", "F01-L2")[0] is None
    assert one("Improved onboarding by five percentage points.", "F01-L3")[0] is None
    assert one("Improved onboarding by 5 points.", "F01-L3")[0] is None
    kept, reason = keep_claim(
        {"text": "You owned the short roadmap.", "evidence_ids": ["F08-L1"], "why": "F08-L1 shows you owned the short roadmap."},
        by, require_citation=True, kind="strength")
    assert kept and kept["evidence_ids"] == ["F08-L1"] and "F08-L1" not in kept["why"]
    uid = "123e4567-e89b-12d3-a456-426614174000"
    kept, reason = keep_claim(
        {"text": "You owned the short roadmap.", "evidence_ids": [uid], "why": f"{uid} shows the roadmap."},
        {uid: {"id": uid, "text": owned}}, require_citation=True, kind="strength")
    assert kept and uid not in kept["text"] and uid not in kept["why"]
    kept, reason = keep_claim(
        {"text": "Ask to own the order of work for one slice of the checklist.", "evidence_ids": ["F01-L1"], "why": "A next step, not a claim of ownership."},
        by, require_citation=False, kind="step")
    assert kept and reason is None
    kept, reason = keep_claim(
        {"text": "In a 30-minute session, rewrite 3-4 items on the checklist you already run.", "evidence_ids": ["F01-L3"], "why": ""},
        by, require_citation=False, kind="step")
    assert kept and reason is None


def test_f06_and_f07_keep_a_real_claim_and_drop_the_denied_one():
    """Round 2 dropped these. Denying the roadmap must not cancel the support queue, and 'to choose' is not ownership."""
    from app.services.ai_assessment.guardrails import keep_claim
    f06 = [{"id": "F06-L2", "text": "Worked with the product manager to explain the charts. I did not choose the roadmap."}]
    by06 = {row["id"]: row for row in f06}
    kept, reason = keep_claim(
        {"text": "This is the work of someone who has to choose what to build.", "evidence_ids": ["F06-L2"],
         "why": "Product managers start from what customers struggle with and then decide what matters."},
        by06, require_citation=True, kind="strength")
    assert kept and reason is None
    dropped, reason = keep_claim(
        {"text": "You chose the roadmap.", "evidence_ids": ["F06-L2"], "why": ""},
        by06, require_citation=True, kind="strength")
    assert dropped is None and reason in {"negation", "collaboration_as_ownership"}
    f07 = [{"id": "F07-L2", "text": "Supported the product team with the top ten ticket themes each month."},
           {"id": "F07-L3", "text": "Did not own the product roadmap. I owned the support queue."}]
    by07 = {row["id"]: row for row in f07}
    kept, reason = keep_claim(
        {"text": "You owned the support queue.", "evidence_ids": ["F07-L3"], "why": "That queue was yours."},
        by07, require_citation=True, kind="strength")
    assert kept and reason is None
    kept, reason = keep_claim(
        {"text": "The support queue shows what a product uses to decide what matters.", "evidence_ids": ["F07-L2"], "why": ""},
        by07, require_citation=True, kind="strength")
    assert kept and reason is None
    dropped, reason = keep_claim(
        {"text": "You owned the product roadmap.", "evidence_ids": ["F07-L3"], "why": ""},
        by07, require_citation=True, kind="strength")
    assert dropped is None and reason == "negation"


def test_later_guardrail_gaps_from_the_probe():
    from app.services.ai_assessment.guardrails import apply_guardrails, keep_claim, remove_ids
    collab = "Worked with the product manager on the roadmap. Did not own the roadmap."
    design = "Rebuilt the checklist with the design team."
    lines = [{"id": "F01-L1", "text": collab}, {"id": "D1", "text": design},
             {"id": "F01-L3", "text": "Raised onboarding completion from 91% to 96%."}]
    by = {row["id"]: row for row in lines}

    def one(text, cited_id, kind="strength"):
        return keep_claim({"text": text, "evidence_ids": [cited_id], "why": ""}, by, require_citation=True, kind=kind)

    for word in ("Spearheaded the pilot.", "Took charge of sprint scope.", "Was accountable for the roadmap.",
                 "Single-handedly rebuilt the checklist."):
        kept, reason = one(word, "F01-L1")
        assert kept is None and reason == "collaboration_as_ownership", word
    assert one("Improved onboarding completion by a fifth.", "F01-L3")[0] is None
    assert one("Improved onboarding by 3x.", "F01-L3")[0] is None
    assert one("Cut the work by half.", "F01-L3")[0] is None
    assert one("Single-handedly rebuilt the checklist, without design.", "D1")[0] is None
    kept, reason = keep_claim(
        {"text": "Next, tell recruiters you owned the roadmap.", "evidence_ids": ["F01-L1"], "why": ""},
        by, require_citation=False, kind="step")
    assert kept is None and reason in {"negation", "collaboration_as_ownership"}
    kept, reason = keep_claim(
        {"text": "Keep owning the roadmap you already own.", "evidence_ids": ["F01-L1"], "why": ""},
        by, require_citation=False, kind="step")
    assert kept is None
    kept, reason = keep_claim(
        {"text": "Write 3-4 things the team should do next.", "evidence_ids": ["F01-L3"], "why": ""},
        by, require_citation=False, kind="step")
    assert kept and reason is None
    out = apply_guardrails(
        {"opening": "You shipped the scheduling tool. You improved completion by 40%.",
         "strengths": [{"text": "You owned the short roadmap.", "evidence_ids": ["F08-L1"],
                        "why": "You are not expected to own a company metric. The short roadmap was yours."}],
         "steps": [{"text": "Ask to own the next slice. Treat that as a request, not an opening.",
                    "evidence_ids": ["F01-L1"], "why": "A next step."}]},
        lines + [{"id": "F08-L1", "text": "Built and shipped a scheduling tool and owned its short roadmap."}])
    assert out["opening"] == "You shipped the scheduling tool."
    assert "40%" not in out["opening"]
    assert "not expected" not in out["strengths"][0]["why"]
    assert "Treat that" not in out["steps"][0]["text"]
    assert "Ask to own the next slice" in out["steps"][0]["text"]
    assert remove_ids("See F09-L2: no roadmap ownership yet", ["F09-L2"]) == "no roadmap ownership yet"
    assert remove_ids("You owned a roadmap", ["a"]) == "You owned a roadmap"


def test_round4_quarter_advice_and_measured_strengths():
    from app.services.ai_assessment.guardrails import collaboration_as_ownership, invented_number, keep_claim
    from app.services.ai_assessment.service import SYSTEM
    metric = "Used the loss-ratio watch again to sequence the next quarter."
    assert not invented_number("You used it again to sequence the next quarter.", metric)
    assert not invented_number("sequencing a quarter", metric)
    assert invented_number("Improved onboarding by a quarter.", "Raised onboarding completion from 91% to 96%.")
    assert invented_number("Improved onboarding by a fifth.", "Raised onboarding completion from 91% to 96%.")
    f03 = "Reported weekly numbers to operations. I did not define the metric."
    kept, reason = keep_claim(
        {"text": "The weekly numbers you report can become a measure you choose on your own slice.",
         "evidence_ids": ["F03-L3"], "why": "A future measure, not one you already defined."},
        {"F03-L3": {"id": "F03-L3", "text": f03}}, require_citation=True, kind="transferable")
    assert kept and reason is None
    f15 = "I have not coached another product manager, and I do not cover a second product."
    kept, reason = keep_claim(
        {"text": "This quarter, start coaching one product person on a requirement you already write.",
         "evidence_ids": ["F15-L3"], "why": "Coaching is the senior gap."},
        {"F15-L3": {"id": "F15-L3", "text": f15}}, require_citation=False, kind="step")
    assert kept and reason is None, reason
    design = "Partnered with design on the error states. The designer owned the layout."
    assert not collaboration_as_ownership(
        "Partnering with design shows you can work a decision with design even when the designer owns the layout.",
        design)
    kept, reason = keep_claim(
        {"text": "Partnering with design shows you can work a decision with design even when the designer owns the layout.",
         "evidence_ids": ["F21-L6"], "why": "The layout stayed with design."},
        {"F21-L6": {"id": "F21-L6", "text": design}}, require_citation=True, kind="transferable")
    assert kept and reason is None
    pad = " ".join(["detail"] * 40)
    many = {
        "opening": "You raised the retry rate. The gap is a written trade-off.",
        "strengths": [
            {"text": "You raised the retry job from 91% to 96% by fixing the timeout.", "evidence_ids": ["Lmet"], "why": "A measured slice."},
            {"text": "You cut the manual refund steps from six screens to two.", "evidence_ids": ["Lsix"], "why": "A before and after on the work."},
            {"text": f"You wrote the notes the squad used the next day. {pad}", "evidence_ids": ["Lplain"], "why": pad},
            {"text": f"You kept a personal list of seller complaints. {pad}", "evidence_ids": ["Lplain"], "why": pad},
        ],
        "transferable": [{"text": f"The notes carry into discovery. {pad}", "evidence_ids": ["Lplain"], "why": pad}],
        "gaps": [{"text": f"You have not written the trade-off. {pad}", "why": pad}],
        "questions": [{"text": f"What did the squad do with the notes? {pad}", "evidence_id": "Lplain"} for _ in range(3)],
        "steps": [
            {"text": "In about two weeks, write a generic teardown of the notes.", "evidence_ids": ["Lplain"], "why": "A template."},
            {"text": "This week, ask to own the next change to the notes you already wrote.", "evidence_ids": ["Lplain"], "why": "In the job."},
        ],
    }
    capped = apply_guardrails(many, [
        {"id": "Lmet", "text": "Raised the success rate of the retry job from 91% to 96%."},
        {"id": "Lsix", "text": "Cut the manual refund steps from six screens to two."},
        {"id": "Lplain", "text": "Wrote the notes the squad used the next day."},
    ])
    kept_text = " ".join(item["text"] for item in capped["strengths"])
    assert "91%" in kept_text and "six screens" in kept_text
    assert len(capped["questions"]) == 1
    assert not any("In about two weeks, write" in step["text"] for step in capped["steps"])
    assert capped["steps"]
    leaked = apply_guardrails(
        {"steps": [{"text": "Ask to own the next slice, without saying you already owned the roadmap.",
                    "evidence_ids": ["Lplain"], "why": ""}]},
        [{"id": "Lplain", "text": "Wrote the notes the squad used the next day."}])
    assert leaked["steps"] and "without saying" not in leaked["steps"][0]["text"]
    assert "without saying" in SYSTEM


def test_the_prompt_hides_rubric_notes_and_limits_india_routes():
    from app.services.ai_assessment.rubric import requirements_for
    from app.services.ai_assessment.service import SYSTEM, _user_prompt
    low = SYSTEM.lower()
    assert "at most one" in low
    assert 'never write "you are not expected to"' in low
    assert "current manager" in low
    text = _user_prompt("PM-APM", "non_pm_to_apm", [{"id": "L1", "text": "Shipped it.", "job": "SWE"}],
                        requirements_for("PM-APM"), None, "all_lines")
    assert "context for you, do not repeat" in text


def test_short_profiles_keep_every_excerpt_and_long_ones_keep_a_denial():
    from app.services.ai_assessment.rubric import requirements_for
    from app.services.ai_assessment.select import select_lines
    short = [{"id": f"S{i}", "text": f"Did task {i}.", "job": "Role"} for i in range(3)]
    shown, info = select_lines(short, requirements_for("PM-APM"), None, None)
    assert info["mode"] == "all_lines" and len(shown) == 3
    long = [{"id": f"L{i}", "text": f"Updated an internal document with the team on day {i}.", "job": "Role"} for i in range(12)]
    long[4] = {"id": "Lneg", "text": "Worked with the product manager. Did not own the roadmap.", "job": "Role"}
    long[9] = {"id": "Lmet", "text": "Raised completion from 70% to 81%.", "job": "Role"}
    shown, info = select_lines(long, requirements_for("PM-PM"), None, None)
    ids = {row["id"] for row in shown}
    assert "Lneg" in ids and "Lmet" in ids
    assert "L2" not in requirements_for("PM-APM") and "L2" not in {r["requirement_key"] for r in requirements_for("PM-PM")}
    assert "L2" in {r["requirement_key"] for r in requirements_for("PM-SPM")}
    assert "C2" not in {r["requirement_key"] for r in requirements_for("PM-APM")}
    assert "C2" in {r["requirement_key"] for r in requirements_for("PM-PM")}


def test_twenty_fictional_personas_cover_the_three_moves():
    assert len(PERSONAS) == 25
    long = [p for p in PERSONAS if len(lines_for(p)) >= 10]
    assert len(long) >= 5
    assert sum(1 for p in long if p["move"].startswith("non_pm")) >= 2
    moves = {p["move"] for p in PERSONAS}
    assert moves == {"non_pm_to_apm", "non_pm_to_pm", "apm_to_pm", "pm_to_spm"}
    assert all(p["fictional"] and "not a real person" in p["header"] for p in PERSONAS)
    assert all(p["city"] for p in PERSONAS)
    for persona in PERSONAS:
        lines = lines_for(persona)
        assert lines and all(line["id"].startswith(persona["id"]) for line in lines)
    doc = parse_document(Document("docx", 1, "docx", [
        Line(text="FICTIONAL RESUME — Asha Menon is not a real person.", index=0),
        Line(text="Professional Experience", index=1, bold=True),
        Line(text="Software Engineer | Northwind Labs | Jan 2022 – Present", index=2),
        Line(text="Raised onboarding completion from 91% to 96%.", bullet=True, index=3),
        Line(text="Education", index=4, bold=True),
        Line(text="B.Tech, Computer Science, Kestrel Valley Institute, 2016 – 2020", index=5),
    ]))
    assert doc.work and doc.education
    assert doc.education[0].start.precision == "year"
