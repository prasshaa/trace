"""Skill normalisation: a curated vocabulary with categories and aliases, plus a strict validity filter.

Two sources, two rules (precision first):
  - a resume's skills section: each top-level item (separators outside brackets only, so "(precision, recall, F1)"
    stays one item) is matched to the vocabulary; an item the vocabulary doesn't know is kept only if it looks like a
    skill name (valid_unknown) and is marked unrecognised so the user checks it;
  - experience bullets and descriptions: ONLY vocabulary terms are taken (a demonstrated capability with its bullet as
    evidence). Free phrases from sentences never become skills.
Synonyms collapse to one canonical name ("JS" -> JavaScript, "Postgres" -> PostgreSQL, "A/B tests" -> A/B testing).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# canonical: (category, [aliases]); matching is case-insensitive except for CASE_SENSITIVE aliases.
VOCAB: Dict[str, Tuple[str, List[str]]] = {
    # Programming and engineering
    "Python": ("Programming", ["python3", "python 3"]), "Java": ("Programming", []),
    "JavaScript": ("Programming", ["js", "javascript (es6)", "es6"]), "TypeScript": ("Programming", ["ts"]),
    "Go": ("Programming", ["golang"]), "C++": ("Programming", ["cpp"]), "C#": ("Programming", ["c sharp", ".net c#"]),
    "Kotlin": ("Programming", []), "Swift": ("Programming", []), "Ruby": ("Programming", []), "PHP": ("Programming", []),
    "Scala": ("Programming", []), "Rust": ("Programming", []), "R": ("Programming", []),
    "SQL": ("Data and analytics", ["structured query language", "t-sql", "pl/sql", "mysql queries"]),
    "React": ("Programming", ["react.js", "reactjs"]), "Angular": ("Programming", ["angularjs"]),
    "Node.js": ("Programming", ["node", "nodejs"]), "Django": ("Programming", []), "Flask": ("Programming", []),
    "Spring Boot": ("Programming", ["spring"]), "FastAPI": ("Programming", []), "HTML": ("Programming", ["html5"]),
    "CSS": ("Programming", ["css3"]), "REST APIs": ("Programming", ["rest", "restful apis", "rest api", "apis", "api design"]),
    "GraphQL": ("Programming", []), "Microservices": ("Programming", ["microservice architecture"]),
    "System design": ("Programming", ["systems design"]), "Git": ("Tools", ["github", "gitlab"]),
    "Selenium": ("Quality and testing", []), "Test automation": ("Quality and testing", ["automation testing", "automated testing"]),
    "Manual testing": ("Quality and testing", []), "JUnit": ("Quality and testing", []), "Cypress": ("Quality and testing", []),
    "Unit testing": ("Quality and testing", ["unit tests"]),
    # Cloud and infrastructure
    "AWS": ("Cloud and infrastructure", ["amazon web services"]), "Azure": ("Cloud and infrastructure", ["microsoft azure"]),
    "Google Cloud": ("Cloud and infrastructure", ["gcp", "google cloud platform"]), "Docker": ("Cloud and infrastructure", []),
    "Kubernetes": ("Cloud and infrastructure", ["k8s"]), "CI/CD": ("Cloud and infrastructure", ["ci cd", "continuous integration"]),
    "Terraform": ("Cloud and infrastructure", []), "Linux": ("Cloud and infrastructure", []),
    "Kafka": ("Cloud and infrastructure", ["apache kafka"]), "Redis": ("Cloud and infrastructure", []),
    # Data and analytics
    "PostgreSQL": ("Data and analytics", ["postgres"]), "MySQL": ("Data and analytics", []), "MongoDB": ("Data and analytics", ["mongo"]),
    "Excel": ("Data and analytics", ["ms excel", "microsoft excel", "advanced excel"]),
    "Tableau": ("Data and analytics", []), "Power BI": ("Data and analytics", ["powerbi"]), "Looker": ("Data and analytics", []),
    "Google Analytics": ("Data and analytics", ["ga4"]), "Mixpanel": ("Data and analytics", []), "Amplitude": ("Data and analytics", []),
    "Data analysis": ("Data and analytics", ["data analytics", "analytics"]), "Data visualisation": ("Data and analytics", ["data visualization", "dashboards", "dashboarding"]),
    "Statistics": ("Data and analytics", ["statistical analysis"]), "Machine learning": ("Data and analytics", ["ml"]),
    "Deep learning": ("Data and analytics", []), "Pandas": ("Data and analytics", []), "NumPy": ("Data and analytics", []),
    "Spark": ("Data and analytics", ["apache spark", "pyspark"]), "Snowflake": ("Data and analytics", []),
    "BigQuery": ("Data and analytics", []), "ETL": ("Data and analytics", ["data pipelines"]),
    "Natural language processing": ("Data and analytics", ["nlp"]), "SAS": ("Data and analytics", []),
    "Financial modelling": ("Business and strategy", ["financial modeling", "financial models"]), "Forecasting": ("Data and analytics", []),
    # Product
    "Product management": ("Product", []), "Product strategy": ("Product", []), "Product roadmaps": ("Product", ["roadmapping", "product roadmap", "roadmap planning", "roadmap", "roadmaps"]),
    "Product discovery": ("Product", ["discovery"]), "Prioritisation": ("Product", ["prioritization", "backlog prioritisation", "backlog prioritization", "prioritised", "prioritized"]),
    "Requirements gathering": ("Product", ["requirements analysis", "requirement gathering", "requirements elicitation"]),
    "PRDs": ("Product", ["product requirement documents", "product requirements documents", "prd"]),
    "User stories": ("Product", []), "A/B testing": ("Product", ["ab testing", "a/b tests", "split testing", "experimentation"]),
    "Go-to-market": ("Product", ["gtm", "go to market", "go-to-market strategy"]), "Product analytics": ("Product", []),
    "Agile": ("Delivery", ["agile methodology", "agile methodologies"]), "Scrum": ("Delivery", []), "Kanban": ("Delivery", []),
    "Jira": ("Tools", []), "Confluence": ("Tools", []), "Notion": ("Tools", []), "Asana": ("Tools", []), "Trello": ("Tools", []),
    "Figma": ("Design and research", []), "Sketch": ("Design and research", []), "Adobe XD": ("Design and research", []),
    "Miro": ("Tools", []), "Postman": ("Tools", []), "Salesforce": ("Tools", []), "HubSpot": ("Tools", []),
    "SAP": ("Tools", []), "Zendesk": ("Tools", []),
    # Design and research
    "User research": ("Design and research", ["customer research", "user interviews", "customer interviews"]),
    "Usability testing": ("Design and research", []), "Wireframing": ("Design and research", ["wireframes"]),
    "Prototyping": ("Design and research", ["prototypes"]), "UX design": ("Design and research", ["user experience design", "ux"]),
    "UI design": ("Design and research", ["ui"]), "Design thinking": ("Design and research", []),
    "Interaction design": ("Design and research", []),
    # Business, delivery and leadership
    "Stakeholder management": ("Business and strategy", ["stakeholder engagement", "managing stakeholders"]),
    "Business analysis": ("Business and strategy", []), "Process improvement": ("Business and strategy", ["process optimisation", "process optimization"]),
    "Market research": ("Business and strategy", []), "Competitive analysis": ("Business and strategy", ["competitor analysis"]),
    "Strategy": ("Business and strategy", ["business strategy", "strategic planning"]), "Pricing": ("Business and strategy", ["pricing strategy"]),
    "P&L management": ("Business and strategy", ["p&l", "p&l ownership"]), "Budgeting": ("Business and strategy", []),
    "Operations management": ("Business and strategy", []), "Supply chain": ("Business and strategy", ["supply chain management"]),
    "Digital marketing": ("Marketing and sales", []), "SEO": ("Marketing and sales", ["search engine optimisation", "search engine optimization"]),
    "Content marketing": ("Marketing and sales", []), "Growth marketing": ("Marketing and sales", ["growth"]),
    "Performance marketing": ("Marketing and sales", []), "B2B sales": ("Marketing and sales", []),
    "Account management": ("Marketing and sales", []), "Customer success": ("Marketing and sales", []),
    "CRM": ("Marketing and sales", []), "Project management": ("Delivery", ["project planning"]),
    "Program management": ("Delivery", ["programme management"]), "Risk management": ("Delivery", []),
    "Release management": ("Delivery", []), "Vendor management": ("Delivery", []),
    "Team leadership": ("Leadership and communication", ["people management", "team management", "leadership"]),
    "Mentoring": ("Leadership and communication", ["coaching", "mentored", "mentorship"]), "Communication": ("Leadership and communication", ["communication skills"]),
    "Presentation skills": ("Leadership and communication", ["presentations"]), "Negotiation": ("Leadership and communication", []),
    "Cross-functional collaboration": ("Leadership and communication", ["cross functional collaboration", "cross-functional teams"]),
    "Problem solving": ("Leadership and communication", ["problem-solving"]),
}
CASE_SENSITIVE = {"R", "Go", "SAS", "ML", "UI", "UX", "SAP", "CRM", "SEO", "ETL", "GTM", "PRD", "NLP", "TS", "JS"}
# Aliases that are too generic to be taken from sentences (only accepted as a whole skills-list item).
LIST_ONLY = {"r", "go", "node", "rest", "apis", "growth", "analytics", "discovery", "ui", "ux", "ml", "ts", "js", "ga4",
             "spring", "leadership", "coaching", "presentations", "strategy", "communication", "pricing", "experimentation",
             "prototypes", "wireframes", "prd", "gtm", "crm", "sap", "seo", "budgeting", "forecasting", "statistics", "roadmap", "roadmaps", "dashboards", "dashboarding", "prioritised", "prioritized"}

ALIASES: Dict[str, str] = {}
for _canon, (_cat, _al) in VOCAB.items():
    for _a in [_canon, *_al]:
        ALIASES[_a.lower()] = _canon


def category(name: str) -> str:
    return VOCAB[name][0] if name in VOCAB else "Other"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip(" .;:-–—•*\t")).strip()


def canonical(item: str) -> Optional[str]:
    t = _norm(item)
    if not t:
        return None
    hit = ALIASES.get(t.lower())
    if hit is None:
        return None
    if t.lower() in ("r", "go") and t not in ("R", "Go", "GO"):      # "r" / "go" in lower case aren't the languages
        return None
    return hit


FRAGMENT_END = re.compile(r"-(based|driven|led|focused|oriented|centric|first|aware|ready)$", re.I)
SENTENCE_WORD = re.compile(r"\b(i|we|my|our|the|was|were|is|are|led|managed|built|drove|increased|reduced|improved|delivered|"
                           r"launched|worked|using|with|for|and|to|of|in|on|by|from|which|that)\b", re.I)


def valid_unknown(item: str) -> bool:
    """An item from a skills list that the vocabulary doesn't know, kept only if it reads as a skill name."""
    t = _norm(item)
    if not (2 <= len(t) <= 35) or not re.match(r"^[A-Za-z]", t):
        return False
    words = t.split()
    if len(words) > 4:
        return False
    if t.count("(") != t.count(")") or t.count("[") != t.count("]"):
        return False                                             # "F1)", "(precision"
    if FRAGMENT_END.search(t) and len(words) == 1:
        return False                                             # "Confidence-Based"
    if re.search(r"[%$₹=<>@#]|\d{3,}|https?:|www\.", t):
        return False                                             # metrics, money, links
    if len(SENTENCE_WORD.findall(t)) >= 1 and len(words) >= 3:
        return False                                             # a sentence fragment
    if t.endswith((",", "-", "/", "&")) or t.lower() in {"etc", "and", "others", "tools", "skills", "languages", "metrics",
                                                          "data", "management", "analysis", "technologies", "frameworks",
                                                          "platforms", "domain", "domains", "other", "misc", "soft skills"}:
        return False
    return True


def split_items(text: str) -> List[str]:
    """Split a skills line on top-level separators only (never inside brackets)."""
    text = re.sub(r"^[^:]{1,40}:\s*", "", text.strip())             # "Tools: SQL, Python" -> "SQL, Python"
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;|•·▪●":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    items = []
    for p in out:
        p = _norm(p)
        if p:
            items.append(p)
    return items


def from_list_item(item: str) -> Tuple[Optional[str], bool]:
    """(name, recognised) for one skills-list item, or (None, False) when it is not a skill."""
    c = canonical(item)
    if c:
        return c, True
    base = re.sub(r"\s*\([^)]*\)\s*$", "", item).strip()            # "Python (Pandas, NumPy)" -> "Python"
    c = canonical(base) if base != item else None
    if c:
        return c, True
    if valid_unknown(base):
        return _norm(base), False
    return None, False


_PATTERNS: List[Tuple[re.Pattern, str]] = []
for _alias, _canon in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
    if _alias in LIST_ONLY or len(_alias) <= 2:
        continue
    if _alias.upper() in CASE_SENSITIVE or _canon in CASE_SENSITIVE and _alias == _canon.lower():
        _PATTERNS.append((re.compile(rf"(?<![\w/+#.]){re.escape(_alias.upper())}(?![\w+#])"), _canon))
    else:
        _PATTERNS.append((re.compile(rf"(?<![\w/+#.]){re.escape(_alias)}(?![\w+#])", re.I), _canon))


def from_sentence(text: str) -> List[str]:
    """Vocabulary skills demonstrated in a sentence (a bullet or description). Longest aliases first, no overlaps."""
    found, taken = [], []
    for rx, canon in _PATTERNS:
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            if canon not in found:
                found.append(canon)
    return found
