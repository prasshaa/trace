"""Deterministic detection and removal of DIRECT personal identifiers in career text.

Removed wherever they occur (any field, any position): email addresses, phone numbers, profile and personal links
(LinkedIn, GitHub, GitLab, X/Twitter, Instagram, Facebook, Medium, Behance, Dribbble, Kaggle, Stack Overflow and any
other URL), social handles, government or personal ID numbers (Aadhaar, PAN, passport, SSN formats), address-like
lines (a 6-digit PIN code next to address words, or a known address line), and the resume owner's known names (the
full name and each name part).

NOT removed: employer, university and organisation names, qualification names, grades and scores, dates, job titles,
skills, responsibilities, achievements and other career facts. A name part that also appears in a protected
career term (an employer or institution name in the same record) is NOT removed automatically: it is reported as
ambiguous, for human review.

Regular expressions cannot recognise every personal name; the owner's known identity (from restricted processing)
closes most of that gap, and every ambiguous match goes to a person. Findings carry categories and counts only, never
the matched values.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Set, Tuple

REPLACEMENT = {
    "email": "[email removed]", "phone": "[phone removed]", "link_linkedin": "[link removed]", "link_github": "[link removed]",
    "link_social": "[link removed]", "link_other": "[link removed]", "handle": "[handle removed]", "id_number": "[id removed]",
    "address": "[address removed]", "name": "[name removed]",
}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SOCIAL_HOSTS = {
    "link_linkedin": r"linkedin\.com",
    "link_github": r"github\.com|github\.io",
    "link_social": r"gitlab\.com|twitter\.com|x\.com|instagram\.com|facebook\.com|fb\.com|medium\.com|behance\.net|"
                   r"dribbble\.com|kaggle\.com|stackoverflow\.com|youtube\.com|t\.me|threads\.net|linktr\.ee|about\.me",
}
URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:[a-z0-9-]+\.)+(?:com|in|io|me|dev|net|org|co|app|page|site|xyz|tech)"
                 r"/[^\s<>\"')\]]*")
BARE_PROFILE = re.compile(r"(?i)\b(?:linkedin\.com/in|github\.com|gitlab\.com|twitter\.com|x\.com|instagram\.com|medium\.com/@)"
                          r"/?[\w.-]+")
HANDLE = re.compile(r"(?<![\w.@/])@[A-Za-z0-9_](?:[A-Za-z0-9_.]{1,29})\b")
PHONE_CANDIDATE = re.compile(r"(?<![\w/])(\+?\(?\d[\d\s().-]{7,}\d)(?![\w/])")
ID_PATTERNS = [
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),                 # Aadhaar (12 digits)
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),                         # PAN
    re.compile(r"\b[A-PR-WYa-pr-wy][1-9]\d\s?\d{4}[1-9]\b"),       # Indian passport
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                          # SSN
]
PIN_CODE = re.compile(r"\b\d{3}\s?\d{3}\b")
ADDRESS_WORDS = re.compile(r"(?i)\b(road|rd\.?|street|st\.|nagar|sector|apartment|apartments|apt\.?|flat|colony|lane|layout|"
                           r"block|house|h\.?\s?no|floor|phase|cross|main road|society|tower|near|opp\.?|district|pin(?:code)?)\b")
DATE_RANGE = re.compile(r"^\s*\(?\d{4}\)?\s*[-–/]\s*\(?\d{4}\)?\s*$")
WORD = re.compile(r"[^\W\d_]+")


def fold(text: str) -> str:
    """Lower case without accents ('Méera' -> 'meera'), for comparing names."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def _within_one_edit(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            edits += 1
            if edits > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
        else:
            i += 1
            j += 1
    return edits + (len(b) - j) + (len(a) - i) <= 1


@dataclass
class Identity:
    """The resume owner's direct identifiers, known only during restricted processing. Never stored."""
    full_name: str = ""
    other_names: Sequence[str] = ()
    emails: Sequence[str] = ()
    phones: Sequence[str] = ()
    handles: Sequence[str] = ()
    urls: Sequence[str] = ()
    address_lines: Sequence[str] = ()

    def names(self) -> List[str]:
        return [n for n in [self.full_name, *self.other_names] if n and n.strip()]

    def name_tokens(self) -> Set[str]:
        return {t.lower() for n in self.names() for t in re.split(r"[\s.,'-]+", n) if len(t) >= 3}


@dataclass
class Findings:
    removed: dict = field(default_factory=dict)        # category -> count
    ambiguous: dict = field(default_factory=dict)      # category -> count (human review)

    def add(self, category: str, n: int = 1, ambiguous: bool = False) -> None:
        target = self.ambiguous if ambiguous else self.removed
        target[category] = target.get(category, 0) + n

    def merge(self, other: "Findings") -> None:
        for k, v in other.removed.items():
            self.add(k, v)
        for k, v in other.ambiguous.items():
            self.add(k, v, ambiguous=True)

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def _url_category(url: str) -> str:
    for cat, hosts in SOCIAL_HOSTS.items():
        if re.search(rf"(?i)(^|[/.]){hosts}(?:/|$)", url):
            return cat
    return "link_other"


def _sub(pattern, text, category, findings, keep=lambda m: False):
    def repl(m):
        if keep(m):
            return m.group(0)
        findings.add(category)
        return REPLACEMENT[category]
    return pattern.sub(repl, text)


def redact(text: str, identity: Identity, protected_terms: Iterable[str] = ()) -> Tuple[str, Findings]:
    """Remove direct identifiers from one text value. protected_terms: employer/institution names of the same record,
    which are kept even when they contain a name part (reported as ambiguous instead)."""
    f = Findings()
    if not text:
        return text, f
    out = text
    # Known exact values first (also catches values the patterns would miss, e.g. an unusual phone layout).
    for value, cat in ([(e, "email") for e in identity.emails] + [(p, "phone") for p in identity.phones]
                       + [(u, "link_other") for u in identity.urls] + [(a, "address") for a in identity.address_lines]):
        if value and value.strip():
            pat = re.compile(re.escape(value.strip()), re.I)
            n = len(pat.findall(out))
            if n:
                out = pat.sub(REPLACEMENT[cat], out)
                f.add(cat if cat != "link_other" else _url_category(value), n)
    out = _sub(EMAIL, out, "email", f)

    def url_repl(m):
        cat = _url_category(m.group(0))
        f.add(cat)
        return REPLACEMENT[cat]
    out = URL.sub(url_repl, out)
    out = BARE_PROFILE.sub(url_repl, out)
    for h in identity.handles:
        if h and h.strip():
            pat = re.compile(rf"(?<![\w])@?{re.escape(h.strip().lstrip('@'))}(?![\w])", re.I)
            if h.strip().startswith("@") or len(h.strip()) >= 4:
                n = len(pat.findall(out))
                if n:
                    out = pat.sub(REPLACEMENT["handle"], out)
                    f.add("handle", n)
    out = _sub(HANDLE, out, "handle", f)
    for pat in ID_PATTERNS:
        out = _sub(pat, out, "id_number", f)
    out = _sub(PHONE_CANDIDATE, out, "phone", f,
               keep=lambda m: not (10 <= _digits(m.group(1)) <= 13) or bool(DATE_RANGE.match(m.group(1))))
    # Address-like lines: a PIN code together with address words on the same line.
    lines = out.split("\n")
    for i, line in enumerate(lines):
        if PIN_CODE.search(line) and ADDRESS_WORDS.search(line):
            lines[i] = REPLACEMENT["address"]
            f.add("address")
    out = "\n".join(lines)
    # The owner's names: full names, then single name parts unless they belong to a protected career term.
    for name in sorted(identity.names(), key=len, reverse=True):
        pat = re.compile(r"(?<![\w])" + r"\s+".join(map(re.escape, name.split())) + r"(?![\w])", re.I)
        n = len(pat.findall(out))
        if n:
            out = pat.sub(REPLACEMENT["name"], out)
            f.add("name", n)
    protected = " ".join(protected_terms).lower()
    for token in sorted(identity.name_tokens(), key=len, reverse=True):
        pat = re.compile(rf"(?<![\w]){re.escape(token)}(?![\w])", re.I)
        hits = pat.findall(out)
        if not hits:
            continue
        if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", protected):
            f.add("name_part_in_career_term", len(hits), ambiguous=True)        # e.g. a surname that is also an employer word
            continue
        out = pat.sub(REPLACEMENT["name"], out)
        f.add("name", len(hits))
    out = _name_variants(out, identity, protected, f)
    return out, f


def _name_variants(text: str, identity: Identity, protected: str, f: Findings) -> str:
    """Spellings of the owner's name parts that exact matching misses. Accent and case variants ('Méera') are certain and
    removed. A capitalised word one edit away from a name part of at least 5 letters ('Kulkarnii') is uncertain: it is
    kept and reported as ambiguous, so a person decides. Words of protected career terms are never touched."""
    tokens = {fold(t) for t in identity.name_tokens()}
    if not tokens:
        return text
    protected_words = {fold(w) for w in WORD.findall(protected)}

    def repl(m):
        word = m.group(0)
        w = fold(word)
        if len(w) < 3 or w in protected_words:
            return word
        if w in tokens:
            f.add("name")
            return REPLACEMENT["name"]
        if word[0].isupper() and len(w) >= 5 and any(len(t) >= 5 and _within_one_edit(w, t) for t in tokens):
            f.add("possible_name_variant", ambiguous=True)
        return word
    return WORD.sub(repl, text)


def residual(text: str, identity: Identity, protected_terms: Iterable[str] = ()) -> Findings:
    """Second pass on already de-identified text: anything still found is a residual identifier (or ambiguity)."""
    cleaned, f = redact(text, identity, protected_terms)
    return f
