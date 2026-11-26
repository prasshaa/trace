"""Trace's reading of a confirmed experience line (Phase 5, D50). Deterministic and local: no external AI service and no vector search.

The capability assessment used to read only the user's own categorisation of each line: a confirmed line that the user
hadn't categorised said nothing, so a profile full of roadmap and strategy work could show "not yet established". This
module reads the MEANING of a line the way a reviewer would, with a curated vocabulary instead of a model:

  * an ACTION governs a RESPONSIBILITY: "Owned product strategy and roadmap", "built the business case", "defined
    accuracy criteria and drove the decision". A responsibility word on its own ("a roadmap tool", "the reporting
    platform") is not evidence that the person did that work;
  * the verb decides the STRENGTH. Owning, defining, leading, driving or building gives "clear" evidence; supporting,
    assisting, contributing to or being exposed to something gives "partial" evidence (it needs more context), and a
    negation ("never", "no", "without") or a learning context ("learned about", "course on") gives none;
  * a few areas need a FACT, not a word (decision rights, D31 and D35): outcome ownership needs a defined metric,
    criterion or target and a decision made with it, or an owned, measured outcome; a quantified result alone is
    partial. A scope question is answered YES only when the text states it ("across 3 teams", "mentored two PMs");
    otherwise it stays unanswered, and the plan asks.

Every match keeps its CUE (the words it rests on) and a plain-language WHY, so each claim in a plan can be traced to the
user's own confirmed text. Titles are never read here (a job title is never evidence of a capability), and absence of a
match is never evidence that the person lacks the experience: the plan says "not yet demonstrated in the available
evidence". The user's own categorisation of a line always takes precedence over this reading.

Quality is measured, not assumed: evaluation/capability_interpretation holds a synthetic, hand-annotated set and its
precision, recall and false-positive results (docs/CAPABILITY_ASSESSMENT.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

INTERPRETER_VERSION = "trace-reading-1.1"

# ----------------------------------------------------------------------------------------------- verbs and their weight
_CLEAR = r"""own(?:ed|s|ing)?|led|lead(?:s|ing)?|drove|driv(?:e|es|ing|en)|defin(?:ed|e|es|ing)|set|sets|setting|shap(?:ed|e|es|ing)|
    buil[td]|build(?:s|ing)?|launch(?:ed|es|ing)?|ship(?:ped|s|ping)?|creat(?:ed|e|es|ing)|establish(?:ed|es|ing)?|
    develop(?:ed|s|ing)?|design(?:ed|s|ing)?|manag(?:ed|e|es|ing)|ran|run(?:s|ning)?|head(?:ed|s|ing)?|spearhead(?:ed|ing)?|
    championed|propos(?:ed|e|ing)|prioriti[sz](?:ed|e|es|ing)|deliver(?:ed|s|ing)?|scal(?:ed|e|ing)|introduc(?:ed|e|ing)|
    identif(?:ied|y|ies|ying)|conduct(?:ed|s|ing)?|wr(?:ote|ite|ites|iting|itten)|author(?:ed|s|ing)?|plann?(?:ed|ing)?|plan|
    integrat(?:ed|e|ing)|migrat(?:ed|e|ing)|re-?architect(?:ed|ing)?|architect(?:ed|ing)?|implement(?:ed|s|ing)?|
    mentor(?:ed|s|ing)?|coach(?:ed|es|ing)?|hir(?:ed|e|ing)|negotiat(?:ed|e|ing)|present(?:ed|s|ing)?|align(?:ed|s|ing)?|
    coordinat(?:ed|e|es|ing)|facilitat(?:ed|e|ing)|interview(?:ed|s|ing)?|
    analy[sz](?:ed|e|es|ing)|measur(?:ed|e|es|ing)|track(?:ed|s|ing)?|monitor(?:ed|s|ing)?|test(?:ed|s|ing)?|evaluat(?:ed|e|es|ing)|
    validat(?:ed|e|es|ing)|automat(?:ed|e|es|ing)|optimi[sz](?:ed|e|es|ing)|improv(?:ed|e|es|ing)|reduc(?:ed|e|es|ing)|
    cut|cutting|increas(?:ed|e|es|ing)|grew|grow(?:s|ing|n)?|sav(?:ed|e|es|ing)|lift(?:ed|s|ing)?|generat(?:ed|e|es|ing)|
    unlock(?:ed|s|ing)?|achiev(?:ed|e|es|ing)|resolv(?:ed|e|es|ing)|enabl(?:ed|e|es|ing)|onboard(?:ed|s|ing)?|
    co-?founded|founded|communicat(?:ed|e|es|ing)|report(?:ed|s|ing)?|demo(?:ed|s)?|work(?:ed|s|ing)?|ran|took|take|turn(?:ed|ing)?|bring|brought|bringing|rebuil[td]|rebuild(?:s|ing)?|rewr(?:ote|ite|itten)|revamp(?:ed|ing)?|overhaul(?:ed|ing)?|mov(?:ed|e|ing)|port(?:ed|ing)|streamlin(?:ed|e|ing)|standardi[sz](?:ed|e|ing)|rolled|roll(?:s|ing)?|deploy(?:ed|s|ing)?|start(?:ed|s|ing)?|decid(?:ed|e|es|ing)|ch(?:ose|oose|oosing|osen)|select(?:ed|ing)?|compar(?:ed|e|ing)|sequenc(?:ed|e|ing)|rank(?:ed|ing)?|agree(?:d)?|redesign(?:ed|ing)?|lead|served|serv(?:e|ing)|own"""
_WEAK = r"""support(?:ed|s|ing)?|assist(?:ed|s|ing)?|help(?:ed|s|ing)?|contribut(?:ed|e|es|ing)|participat(?:ed|e|es|ing)|
    partner(?:ed|s|ing)?|collaborat(?:ed|e|es|ing)|
    involved|exposure|exposed|shadow(?:ed|ing)?|observ(?:ed|e|ing)|attend(?:ed|s|ing)?|familiar|aware|influenc(?:ed|e|es|ing)|
    input|inputs|responsible|accountable"""
_NEGATE = r"\b(?:never|not|no|without|didn't|did not|wasn't|lack(?:ed|ing)?)\b"
_LEARN = r"\b(?:learn(?:ed|t|ing)?|stud(?:ied|y|ying)|course(?:work)?|certificat(?:e|ion)|training (?:on|in)|read about)\b"

_rx = lambda s: re.compile(s.replace("\n", "").replace(" ", ""), re.I)        # noqa: E731
CLEAR_VERB = _rx(r"\b(?:" + _CLEAR.rstrip("|") + r")\b")
WEAK_VERB = _rx(r"\b(?:" + _WEAK + r")\b")
NEGATE = re.compile(_NEGATE, re.I)
LEARN = re.compile(_LEARN, re.I)
# A headcount ("12 merchants", "40 users") is scope, not a measured outcome. Percentages, money, multipliers
# and an explicit change ("91% to 96%") are.
QUANT = re.compile(r"(?:\d[\d,.]*\s?(?:%|percent|x\b|×|k\b|m\b|mn\b|bn\b|cr\b|crore|lakh|hours?|hrs|days?|weeks?|months?|"
                   r"points?|bps)|[$₹€£]\s?\d|\d+\s?(?:→|->|to)\s?\d+\s?%|0\s?(?:→|->|to)\s?1)",
                   re.I)


# ----------------------------------------------------------------------------------------------- responsibility vocabulary
@dataclass(frozen=True)
class Cue:
    area: str
    pattern: re.Pattern
    why: str                                  # what the line shows, in plain words
    needs_action: bool = True                 # the responsibility must be governed by an action in the same clause
    strength: str = "auto"                    # auto (from the verb) | partial (never more than related evidence)
    lead_ok: bool = False                     # the line's opening verb may govern it (core product work only)


def _cue(area: str, words: str, why: str, **kw) -> Cue:
    return Cue(area, re.compile(r"\b(?:" + words + r")\b", re.I), why, **kw)


CUES: Tuple[Cue, ...] = (
    # direction: roadmap and strategy (C2, L1)
    _cue("roadmap_ownership", r"road-?maps?|product direction|the direction (?:for|of)|release plans?|product plans?|"
         r"quarterly plans?|product vision|"
         r"vision and roadmap|feature roadmap|planning cycles?", "sets or owns a product roadmap or direction"),
    _cue("strategy_portfolio", r"(?:product |go-to-market |platform |growth |business |market )?strateg(?:y|ies|ic plan)|"
         r"business cases?|product lines?|portfolio|growth opportunit(?:y|ies)|new (?:market|category|product line)s?|"
         r"addressable market|market opportunit(?:y|ies)|0\s?(?:→|->|to)\s?1|zero[- ]to[- ]one|from scratch|new product",
         "shapes product strategy, a business case or what to build next"),
    # problems into requirements and priorities (C3)
    _cue("prioritisation_backlog", r"prioriti[sz]ation|prioriti[sz](?:e|ed|ing)|backlogs?|trade-?offs?|"
         r"high-value opportunit(?:y|ies)|what to build", "decides priorities and trade-offs",
         needs_action=False),
    _cue("requirements_analysis", r"PRDs?|product requirements?|requirements|user stories|specs?|specifications|"
         r"acceptance criteria|functional design", "turns problems into requirements and specifications"),
    # users and customers (C1)
    _cue("product_discovery", r"user research|customer research|discovery|customer interviews?|user interviews?|"
         r"customer feedback|user feedback|feedback into the roadmap|workflow friction|pain points?|user needs|"
         r"customer needs|jobs to be done|surface(?:d)? feedback|their feedback|usability", "learns what users and customers need"),
    _cue("design_research", r"UX|UI/UX|user experience|usability|prototypes?|wireframes?|design system|redesign|"
         r"usability score", "works on design and usability"),
    # outcomes and data (C4, X1, X5)
    _cue("outcome_ownership", r"(?:success |outcome |product |north[- ]star |business )?metrics|KPIs?|OKRs?|"
         r"success criteria|(?:accuracy|completeness|evaluation|quality|acceptance) criteria|targets?|rubrics?|"
         r"north[- ]star|goals?", "defines or owns the measures of a product's success"),
    _cue("data_analysis", r"analytics|metrics|dashboards?|reporting|data flows?|SQL|A/B tests?|experiments?|"
         r"experimentation|KPI tracking|performance (?:data|reporting|signals)|insights|funnel|cohort analysis|"
         r"diagnos(?:e|ed|ing) performance|data-(?:driven|informed)", "works with product data, metrics or analysis"),
    _cue("business_case_economics", r"business cases?|revenue|ARR|MRR|margins?|cost(?:s| discrepanc(?:y|ies)| tracking)?|"
         r"pricing|P&L|unit economics|monetis(?:ation|ed)|monetiz(?:ation|ed)|financial|ROI|spend|budget",
         "works on the economics: revenue, cost, pricing or the business case"),
    _cue("go_to_market_growth", r"GTM|go-to-market|launch plans?|adoption|growth|customer acquisition|user acquisition|"
         r"sales enablement|product marketing|activation|retention|referral programmes?|marketing campaigns?",
         "takes a product to market or grows its use"),
    # people (C5, L2, L3)
    _cue("stakeholder_management", r"stakeholders?|cross-functional|with (?:engineering|design|data|sales|marketing|"
         r"operations|analytics|qa|legal|finance)|engineering and|leadership team|alignment|aligned|"
         r"with the (?:\w+ ){0,2}teams?",
         "works across functions and stakeholders"),
    _cue("executive_communication", r"executives?|leadership|senior leaders?|VPs?|CXOs?|C-suite|founders?'?|board|"
         r"heads? of|director(?:s)?|managing director|CEO|CTO|COO|CFO", "communicates with senior leadership"),
    _cue("customer_account_management", r"customers|clients|(?:enterprise|key|client|customer) accounts|client demos?|"
         r"product demos?|customer calls?|account base|customer success|relationship with|prospective clients",
         "works directly with customers or clients"),
    _cue("people_leadership", r"(?:\d+|three|two|four|five|six|several)\s+(?:\w+\s+){0,1}(?:cross-functional\s+)?(?:teams?|engineers|"
         r"members|people|reports|PMs|product managers|analysts)|team of \d+|mentor(?:ed|ing)?|coach(?:ed|ing)?|"
         r"direct reports|hir(?:ed|ing)", "leads or develops people"),
    # delivery (C6, L4)
    _cue("delivery_execution", r"to production|execution|sprints?|agile|scrum|launch readiness|release readiness|rollout|"
         r"go-live|delivery plan", "takes work through to delivery"),
    _cue("operations_process", r"operations|operational|processes|process|workflows?|SOPs?|efficiency|manual effort|"
         r"turnaround", "improves operations, processes or workflows"),
    _cue("quality_testing", r"testing|test cases?|QA|quality assurance|release validation|defects?|regression|"
         r"guardrails|evaluation set|automated scoring|offline evaluation", "works on quality and testing"),
    _cue("technical_engineering", r"APIs?|architectures?|re-?architect(?:ure|ed)?|integrations?|system design|"
         r"pipelines?|platform|microservices|SDKs?|infrastructure|data models?|frontend|backend|framework",
         "works on technical systems: architecture, APIs or integrations"),
    _cue("ai_product_work", r"AI|ML|machine learning|LLMs?|agents?|agentic|RAG|models?|GenAI|generative|NLP|"
         r"LLM-?as-?a-?judge|evals?|prompt|chatbots?|copilots?|recommendation (?:engine|approach|model)s?",
         "works on AI or machine-learning products"),
    _cue("data_governance_quality", r"data quality|governance|privacy|compliance|GDPR|data accuracy|"
         r"audit|data integrity|consent|privacy review", "works on data quality, privacy or governance"),
)

# Phrases that ARE the work (the verb carries the responsibility): clear unless negated, hedged or about learning.
SELF_EVIDENT: Tuple[Tuple[str, re.Pattern, str], ...] = tuple((a, re.compile(p, re.I), w) for a, p, w in (
    ("delivery_execution", r"\b(?:shipp(?:ed|ing)|launch(?:ed|ing)|releas(?:ed|ing)|deliver(?:ed|ing)|rolled out|rolling out|"
                           r"went live|deploy(?:ed|ing))\b", "takes work through to delivery"),
    ("prioritisation_backlog", r"\bdecid(?:ed|ing|es) what\b|\bin what order\b|\bsequenc(?:ed|ing) (?:the |its |their )?"
                               r"(?:work|bets|releases|features|roadmap)", "decides priorities and trade-offs"),
    ("product_discovery", r"\b(?:interview(?:ed|ing)|surveyed|shadowed users)\b", "learns what users and customers need"),
    ("go_to_market_growth", r"\b(?:grew|grow(?:ing)?|built)\b (?:the |a |an |its |it |their )?(?:\w+ ){0,2}?"
                            r"(?:audience|subscribers|listeners|readers|users|sign-?ups|community|followers|downloads|it to)",
     "takes a product to market or grows its use"),
    ("people_leadership", r"\b(?:mentor(?:ed|ing)|coach(?:ed|ing))\b", "leads or develops people"),
    ("strategy_portfolio", r"\b(?:only|sole|lone) (?:product manager|PM)\b[^.;]{0,40}\bproduct (?:line|area)",
     "owns a product area as its only product manager"),
))

# Scope questions (taxonomy v2/v3 qualifiers), answered YES only when the text says so.
QUALIFIERS = {
    ("data_analysis", "outcome_metrics_influence"): re.compile(
        r"(?:defin|set|own|establish|introduc)\w*\s(?:[\w/-]+\s){0,4}(?:metrics|KPIs?|OKRs?|criteria|targets|north[- ]star)"
        r"[^.;]{0,160}(?:decid|decision|prioriti|chose|choos|direction|roadmap|go/no-go|ship|launch|kill|invest)|"
        r"(?:used|using|based on)\s(?:[\w/-]+\s){0,4}(?:data|metrics|analytics|experiments?|insights)\s(?:\w+\s){0,3}"
        r"(?:to\s)?(?:decide|prioriti[sz]e|choose|shape|steer|drive)|data-(?:driven|informed) (?:product )?decisions?|"
        r"drove the decision", re.I),
    ("strategy_portfolio", "multi_product_scope"): re.compile(
        r"(?:across|for|owning|over)\s(?:\w+\s){0,3}(?:\d+\+?|two|three|four|five|several|multiple)\s(?:\w+\s){0,2}"
        r"(?:products|product lines|platforms|markets|countries|business units|portfolios)|portfolio of|"
        r"(?:high-risk|regulated|complex)\s(?:\w+\s){0,2}(?:product|platform)", re.I),
    ("delivery_execution", "cross_team"): re.compile(
        r"(?:\d+|two|three|four|five|several|multiple)\s(?:cross-functional\s)?teams|across teams|cross-team|"
        r"dependencies across|between (?:\w+\s)?teams", re.I),
    ("people_leadership", "coaching_mentoring"): re.compile(r"mentor|coach", re.I),
    ("business_case_economics", "tracks_outcomes"): re.compile(
        r"(?:track|monitor|measur|report)\w*\s(?:[\w/-]+\s){0,5}(?:revenue|cost|margin|spend|ARR|adoption|ROI|targets?)|"
        r"(?:cost|revenue|financial)\s(?:tracking|accountability|reporting)", re.I),
}


@dataclass
class Reading:
    """What one confirmed line shows. `clear` areas count as evidence found; `partial` ones are related evidence that
    needs more context. `qualifiers` holds only answers the text states (True); unknown stays absent."""
    clear: Dict[str, Tuple[str, str]] = field(default_factory=dict)       # area -> (cue words, why)
    partial: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    qualifiers: Dict[str, Dict[str, bool]] = field(default_factory=dict)

    @property
    def areas(self) -> List[str]:
        return sorted(set(self.clear) | set(self.partial))


# Core product work: a resume line's opening verb ("Owned ...", "Built ...") may govern these even a few words away.
# Context nouns (customers, executives, platforms, AI, data, costs) need an action word right before them, so a line that
# only mentions them ("revenue for the current clients") is not read as that kind of work.
LEAD_OK = frozenset({"roadmap_ownership", "strategy_portfolio", "requirements_analysis", "product_discovery", "delivery_execution",
                     "go_to_market_growth", "design_research", "prioritisation_backlog", "data_analysis"})
# Owning these needs an ownership verb. "Worked with", "supported" and "partnered" are collaboration, not ownership.
OWNERSHIP_AREAS = frozenset({"roadmap_ownership", "strategy_portfolio", "outcome_ownership"})
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_WITH = frozenset({"worked", "work", "working", "works"})
LOOKBACK = 8                                   # words before a responsibility phrase that may hold its verb
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\s*[;•]\s*")


_NOUN_AFTER = {"with", "and", "the", "a", "an", "of", "for", "on", "to", "our", "their"}
# Words that are verbs in the list and also ordinary nouns ("with design", "the plan").
_NOUN_VERB = {"design", "designs", "plan", "plans", "set", "run", "work", "works", "report", "reports"}


def _verb_kind(words: Sequence[str], i: int) -> Optional[str]:
    """clear, weak, or None. 'Worked with' is collaboration, not the ownership verb 'worked'."""
    w = words[i]
    nxt = words[i + 1].lower() if i + 1 < len(words) else ""
    prev = words[i - 1].lower() if i else ""
    if w.lower() in _NOUN_VERB and prev in _NOUN_AFTER:
        return None
    if w.lower() in _WITH and nxt == "with":
        return "weak"
    if WEAK_VERB.fullmatch(w):
        return "weak"
    if CLEAR_VERB.fullmatch(w):
        return "clear"
    return None


def _governing(prefix: str) -> Optional[str]:
    """The nearest verb (or negation) among the last LOOKBACK words before a responsibility phrase: clear, weak, neg."""
    words = [m.group(0) for m in WORD.finditer(prefix)]
    for i in range(len(words) - 1, max(-1, len(words) - 1 - LOOKBACK), -1):
        w = words[i]
        if NEGATE.fullmatch(w) or w.lower() in {"didn't", "wasn't", "don't", "isn't"}:
            return "neg"
        kind = _verb_kind(words, i)
        if kind:
            before = words[max(0, i - 3):i]
            if any(NEGATE.fullmatch(x) or x.lower() in {"didn't", "wasn't", "don't"} for x in before):
                return "neg"
            # "did not" is two words; "not" is already caught. A curly apostrophe is normalised before this runs.
            return kind
    return None


def _lead(sentence: str) -> Optional[str]:
    """The opening verb of a resume line or sentence ("Owned", "Built and scaled"), if it has one."""
    words = [m.group(0) for m in WORD.finditer(sentence)][:4]
    if any(NEGATE.fullmatch(w) or w.lower() in {"didn't", "wasn't"} for w in words):
        return None
    for i, w in enumerate(words[:3]):
        kind = _verb_kind(words, i)
        if kind:
            return kind
    return None


def _normalise(text: str) -> str:
    """Straighten apostrophes so "didn't" and "didn’t" negate the same way, and collapse whitespace."""
    return " ".join((text or "").replace("\u2019", "'").replace("\u2018", "'").replace("`", "'").split())


def _retracted(sentence: str) -> set:
    """Areas a later clause takes back: 'built the roadmap but did not own the roadmap'."""
    parts = re.split(r"\b(?:but|although|however|though|except|without)\b", sentence, flags=re.I)
    out = set()
    for span in parts[1:]:
        if not NEGATE.search(span):
            continue
        for cue in CUES:
            if cue.pattern.search(span):
                out.add(cue.area)
    return out


def interpret(text: str, evidence_type: Optional[str] = None) -> Reading:
    """Trace's reading of one confirmed line. Pure and deterministic."""
    out = Reading()
    text = _normalise(text)
    if not text:
        return out
    for sentence in [x for x in SENTENCE.split(text) if x and x.strip()]:
        lead = _lead(sentence)
        learning = LEARN.search(sentence[:60])
        for cue in CUES:
            for m in cue.pattern.finditer(sentence):
                gov = _governing(sentence[:m.start()])
                if gov == "neg" or learning:
                    continue
                if gov is None and cue.area in LEAD_OK and lead:
                    gov = lead
                if gov is None:
                    if cue.needs_action:
                        continue
                    gov = "weak"
                # Collaboration ("supported", "partnered", "worked with") is not ownership of the roadmap,
                # the strategy, or the outcome. It can still be related evidence of another area.
                if gov == "weak" and cue.area in OWNERSHIP_AREAS:
                    continue
                strength = "clear" if gov == "clear" and cue.strength != "partial" else "partial"
                hit = (m.group(0), cue.why)
                if strength == "clear":
                    out.clear.setdefault(cue.area, hit)
                    out.partial.pop(cue.area, None)
                elif cue.area not in out.clear:
                    out.partial.setdefault(cue.area, hit)
    for sentence in [x for x in SENTENCE.split(text) if x and x.strip()]:
        if LEARN.search(sentence[:60]):
            continue
        for area, rx, why in SELF_EVIDENT:
            for m in rx.finditer(sentence):
                prefix = [w.group(0) for w in WORD.finditer(sentence[:m.start()])][-4:]
                if any(NEGATE.fullmatch(w) for w in prefix):
                    continue
                if any(WEAK_VERB.fullmatch(w) for w in prefix):
                    if area not in out.clear:
                        out.partial.setdefault(area, (m.group(0), why))
                    continue
                out.clear.setdefault(area, (m.group(0), why))
                out.partial.pop(area, None)
    for sentence in [x for x in SENTENCE.split(text) if x and x.strip()]:
        for area in _retracted(sentence):
            if any(c.area == area and c.pattern.search(sentence) for c in CUES):
                out.clear.pop(area, None)
                out.partial.pop(area, None)
    _outcomes(text, out)
    for (area, q), rx in QUALIFIERS.items():
        if area in out.clear and rx.search(text) and not NEGATE.search(text[:40]):
            out.qualifiers.setdefault(area, {})[q] = True
    return out


def _outcomes(text: str, out: Reading) -> None:
    """Outcome ownership (C4) needs more than a word. Clear: a defined or owned measure (metric, KPI, criterion, target)
    together with a decision or an owned result; or an owned result that is measured ("Drove ... improving X by 30%").
    A measured result on its own is partial: it shows outcomes, not that the person defined how success is measured."""
    measure = out.clear.get("outcome_ownership") or out.partial.get("outcome_ownership")
    defined = re.search(r"(?:defin|set|own|establish|introduc|built|design|chose|choos|agreed|select)\w*\s(?:[\w/-]+\s){0,5}"
                        r"(?:metrics?|measures|KPIs?|OKRs?|criteria|targets?|rubrics?|north[- ]star|evaluation system)|"
                        r"against (?:\w+ ){0,4}(?:criteria|metrics|targets|benchmarks)", text, re.I)
    decided = re.search(r"decid|decision|prioriti|go/no-go|direction|chose|choos", text, re.I)
    owned_result = re.search(r"^(?:drove|led|owned|delivered|shipped|launched|grew|built|achieved)\b[^.;]{0,200}", text, re.I)
    quant = QUANT.search(text)
    if quant and NEGATE.search(text[max(0, quant.start() - 40):quant.end()]):
        quant = None
    out.clear.pop("outcome_ownership", None)
    out.partial.pop("outcome_ownership", None)
    if defined and (decided or quant) and not NEGATE.search(text[:max(0, defined.start())][-40:]):
        out.clear["outcome_ownership"] = (defined.group(0), "defines how success is measured and acts on it")
    elif quant and owned_result and not NEGATE.search(text[:owned_result.end()]):
        out.partial["outcome_ownership"] = (quant.group(0), "owns a measured result; it doesn't say who defined the measure")
    elif quant:
        out.partial["outcome_ownership"] = (quant.group(0), "shows a measured result; it doesn't say who defined the measure")
    elif measure:
        out.partial["outcome_ownership"] = (measure[0], "works with product metrics; it doesn't show defining them or "
                                                        "deciding with them")


def interpret_many(lines: Sequence[Tuple[str, str, Optional[str]]]) -> Dict[str, Reading]:
    """{record id: Reading} for (record id, text, evidence type) triples."""
    return {rid: interpret(text, kind) for rid, text, kind in lines}
