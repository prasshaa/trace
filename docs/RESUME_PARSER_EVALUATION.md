# Resume parser v2: selection, evaluation and release thresholds

Decision D47. Code:
- `backend/app/services/resume_parsing/`: document, parser, skills, and the isolated runner in `__init__`.
- `backend/evaluation/resume_parser/`: corpus, holdout, render, evaluate.

Tests: `backend/tests/test_resume_parser.py`. Sections 1–5 are the **historical** record of parser 2.0 (measured
2026-11-20, unchanged below). Section 6 is parser 2.1 (D48), measured 2026-11-20/21; section 7 is parser 2.2 (D49), measured 2026-11-22.

## 1. Why the previous ingestion failed (root causes)

| Observed | Cause in code |
|---|---|
| Job descriptions split into fragments | `real_parser.py` read pypdf's flat text line by line. A wrapped line over 60 characters became its own evidence item, and line wraps were never re-joined. |
| "F1)" and "Confidence-Based" accepted as skills | `_skills()` split every skills-section line on every comma, even inside brackets ("(precision, recall, F1)"). It had no vocabulary and no fragment filter, and "tools" or "technologies" lines counted as skills sections. |
| Dozens of "Add and confirm / Add as draft / Dismiss" cards | Pasted text went through `compare()`, which produces one proposal per job, bullet, qualification and skill, each decided separately. |
| Two-column resumes read across columns | pypdf has no layout model, so sidebars were interleaved with the experience column. |
| No way to clear or restart | Dismissed proposals stay as history. The same text was recognised as "already proposed". A profile holding only a rejected skill kept routing back to Get Started, and the onboarding offered nothing to clear. |
| Upload "not available" | Real uploads depend on `TRACE_REAL_RESUME_UPLOADS` and an identity-marker key (contribution safeguards). Neither was set, so pasting was the only path. |

## 2. Candidates evaluated

Document extraction and resume-specific parsing were assessed separately. Facts below come from PyPI, the GitHub API
and the Hugging Face API, retrieved 2026-11-20.

**Document extraction:**

| Candidate | Licence (code; weights) | Latest release / activity | Formats | Layout and reading order | OCR | Footprint | Data leaves the server? | Decision |
|---|---|---|---|---|---|---|---|---|
| **pdfplumber** 0.11.10 | MIT; dependencies MIT, BSD-3, Apache-2.0, MIT-CMU | 2026-06-15; pushed 2026-10 | PDF | Word-level coordinates, fonts, sizes. Columns are reconstructed by Trace. | No | Small (pdfminer.six, pypdfium2, Pillow) | No | **Selected for PDF** |
| **python-docx** 1.2.0 | MIT; lxml BSD-3 | 2025-06-16; pushed 2026-08 | DOCX | Paragraph styles, list numbering, tables | n/a | Small | No | **Selected for DOCX** |
| pypdf 7.x (in use before) | BSD-3 | 2026-11 | PDF | Flat text only; no reading order | No | Small | No | Kept only for encryption and page-count checks |
| Docling 2.151 | MIT; Heron layout weights Apache-2.0 (training data DocLayNet, CDLA-Permissive-1.0) | Daily releases (2026-11-19) | PDF, DOCX, images, more | Best layout model of those assessed | Yes (EasyOCR, Tesseract, RapidOCR options) | torch, torchvision, transformers; model download from the Hugging Face Hub; GBs | Model download at first use (disable Hub access and telemetry, bundle weights) | **Next candidate** for scans and complex layouts. Not adopted now: heavy footprint and network model fetch. |
| unstructured 0.27 | Apache-2.0 | 2026-11 | Many | Good with its hi-res strategy (needs model extras) | Yes (extras) | 114 direct requirements | No if run locally | Not adopted: footprint, and no resume-specific gain over pdfplumber |
| PyMuPDF | AGPL-3.0 | — | — | — | — | — | — | Rejected on licence |

**Resume-specific parsers:**

| Candidate | Licence | Activity | Notes | Decision |
|---|---|---|---|---|
| pyresparser | GPL-3.0 | Last pushed 2023-09 | spaCy-based; unmaintained | Rejected (licence, maintenance) |
| OpenResume | AGPL-3.0 | Last pushed 2024-10 | JavaScript, client-side | Rejected (licence) |
| SmartResume (alibaba) | Apache-2.0 | 2026-06 | Layout model plus an LLM (Qwen) for extraction; GPU-class | Not adopted: LLM extraction falls under D1 and needs model hosting |
| Commercial parsing APIs, external AI | — | — | — | Excluded by D1 |

**Selected:** pdfplumber and python-docx for extraction. Trace owns a deterministic resume segmentation (sections,
wrapped lines, entries, dates, education, activities, skills vocabulary). There are no models and no network access;
it runs in a separate, killable process with a time limit and, on Linux, a memory limit.

## 3. Evaluation design

**Corpus:**
- 10 fictional personas (`corpus.py`), each rendered in 5 layouts (`render.py`): one-column PDF, grouped PDF (an
  employer heading with several dated roles), two-column PDF with a sidebar, DOCX with heading and list styles, and a
  DOCX two-column layout table. That's 50 documents.
- The variations covered:
  - promotions within one company and concurrent roles;
  - a career gap;
  - Indian and abbreviated date formats ("Apr'19", "Till date", MM/YYYY, YYYY-MM, "Month YYYY");
  - internships, freelance work and a founder role;
  - wrapped multi-line bullets and prose descriptions;
  - skills embedded only in bullets;
  - certifications, projects and volunteering;
  - fragment traps ("Metrics (precision, recall, F1)", "Confidence-Based").
- **Held-out set** (`holdout.py`): 3 personas × 5 layouts = 15 documents, written after tuning. It differs in
  conventions: "Sr.", "Mgr", a concurrent volunteer role, a product-owner history.

**Ground truth:** hand-labelled jobs (title, employer, dates), exact bullet texts, descriptions, education,
certifications, projects, expected skills and forbidden fragments. No real resume, private profile or research
record is used.

**Scoring:** structured comparison against the ground truth (no LLM judge), micro-averaged, also reported per layout.

**Thresholds, fixed before the first run:**

| Metric | Threshold |
|---|---|
| Employer precision / recall | ≥ 0.95 / ≥ 0.90 |
| Title precision / recall | ≥ 0.95 / ≥ 0.90 |
| Date accuracy (start and end of matched jobs) | ≥ 0.95 |
| Segmentation (documents with exactly the right jobs) | ≥ 0.90 |
| Bullet grouping (truth bullets extracted whole, once) | ≥ 0.95 |
| Description accuracy | ≥ 0.90 |
| Education (qualification and institution) | ≥ 0.90 |
| Certifications / projects / volunteering | ≥ 0.85 |
| Skill precision / recall | ≥ 0.90 / ≥ 0.75 |
| Skill false-positive rate | ≤ 0.10 |
| Forbidden fragments accepted | 0 |
| Duplicates (jobs or skills) | 0 |
| Fabrication (extracted fact not in the source text) | 0 |

## 4. Results

Run with `cd backend && .venv/bin/python -m evaluation.resume_parser`. The gate is asserted by
`tests/test_resume_parser.py`.

| Stage | Main failures |
|---|---|
| First run (development corpus) | Employer recall 0.45, segmentation 0.30, bullet grouping 0.67, education 0.62, activities 0.27, skill recall 0.73. Precision was already high: no fabrication, duplicates or forbidden fragments. |
| Failure modes found and fixed | See the list below the table. |
| **Development corpus, final** | **All thresholds met.** Employer, title, dates, segmentation, bullets and descriptions 1.000; education 0.983; activities 1.000; skill precision 0.987, recall 0.987; fabrication 0; duplicates 0; forbidden fragments 0. |
| **Held-out, first and only blind run** | Title recall **0.85 (FAIL)**. Every other metric passed: employer 0.975 / 0.975, dates 0.974, segmentation 0.933, bullets 0.963, education 1.0, skill precision 1.0, fabrication 0. The cause was one mode: abbreviated designations ("Operations Mgr") were left unresolved and flagged, never invented. |
| Held-out after the "Mgr/Sr./AVP/DGM" fix | All thresholds met. The set has now informed one fix, so it is **no longer blind**; a fresh held-out set is needed for the next release. |
| Robustness: the 41 existing SYN fixture PDFs (a different template) | No crashes; job count exact 39/41; start dates 99/105; median 10 ms per file (max 98 ms, in process). |

Failure modes found and fixed:
- unmapped bullet glyphs "(cid:127)";
- titles read as employers ("Software", "Analytics" counted as company words);
- the wrong reading order for full-width rows in two-column pages;
- prose continuation that ignored column width;
- dated header rows absorbing the description;
- location-only lines under a job header;
- certificate names containing "–";
- vocabulary gaps ("mentored", "prioritised");
- the case-sensitive ETL match;
- over-general "dashboards" and "Metrics".

## 5. Known weaknesses (not production-ready claims)

- **Scanned or image-only PDFs aren't supported.** They're refused with a clear message, since there's no OCR. Docling
  with local OCR is the candidate.
- **The corpus is synthetic and rendered by Trace.** Real resumes vary more:
  - tables used for dates;
  - icons used as section markers;
  - text boxes;
  - headers and footers carrying content;
  - three-column layouts;
  - non-English text.

  Measured quality on real resumes is unknown until a consented, labelled real-resume set exists. **Resume upload is
  therefore a reviewed draft, not a trusted fact source.** Every field is confirmed by the user.
- **Skills outside the curated vocabulary** are kept only from skills lists, and are marked "not recognised" for the
  user to check.
- **Job type and role match aren't inferred.** Employment type is captured only when the resume states it; the user
  chooses it, and confirms the role match, for the most recent job.
- **Location is extracted but not stored** (data minimisation; not in the canonical record).
- **The memory limit is best effort on macOS.** `RLIMIT_AS` is enforced on Linux. Page (8) and text (60k characters)
  limits apply everywhere.

## 6. Parser 2.1 (D48): the review failure, fixes, a new evaluation split and results

### 6.1 What failed in a review with a real resume

The original file is not retained (by design), so the parser couldn't be re-run on it. The diagnosis comes from the
stored, de-identified draft and the code; no real content is copied into fixtures or documentation.

| Observed | Stage | Cause (verified in the stored draft unless marked) |
|---|---|---|
| A role at an earlier employer appeared inside the next role's description | section detection, then date parsing | The "EARLIER EXPERIENCE" heading wasn't in the heading vocabulary, so it didn't end the previous role. The entry was one paragraph ("Employer, Title, Mon–Mon YYYY. Description"), and a range sharing one year ("Jan–Mar 2021") wasn't a recognised date range, so nothing started a new role. |
| The "Global Product Manager" employer was a description of the business, not the company's name | employment field extraction (employer choice) | The employer line held the company's short name and a tagline ending in "Company". The scorer preferred the fragment with an organisation word, the tagline. A link glyph ("↗") was also kept. De-identification and serialisation were not involved: neither creates such text. |
| Two descriptions started mid-sentence | paragraph joining | One-paragraph entries ("Employer, Title, Mon YYYY – Mon YYYY. Description…"): the text after the dates on the header line was dropped as an unplaced fragment, and a dated line was never allowed to continue into the next line. |
| "LEADERSHIP & RECOGNITION" content inside a role's description | section detection | The heading wasn't in the vocabulary. |
| Spaces before commas ("Employer , Title ,") | PDF extraction | pdfplumber splits a word where the font changes (bold name, regular comma), and the line builder put a space between the pieces. |
| Education had a certification provider as the degree's institution | section detection, education parsing | "Education & Certifications" in one section, and courses attached to the degree. |
| **The most recent role was missing entirely** | **not verifiable** | No trace of it remains in the draft. Paths in 2.0 that drop a role silently, any of which can produce this: text in the experience section before the first recognised role; a dated line with no recognisable title or employer; a role with dates but no title (2.0 kept only titled roles); text above the first heading or in a summary. **2.1 makes every one of these paths visible** (`unresolved`, below), so a re-upload will show where that text goes. |
| Dates of the remaining roles | — | Plausible and consistent with the source lines that survive in the draft; the original can't be checked again without the file. |

### 6.2 Changes (parser 2.1, `PARSER_VERSION` "trace-resume-parser-2.1", draft schema `trace-resume-draft/2`)

- **Extraction:**
  - pieces of one word split at a font change are joined;
  - link and icon glyphs are removed;
  - a narrow sidebar is recognised by its own left edge, while right-aligned dates are not mistaken for a column;
  - a separator glyph on the gutter no longer forces a full-width row;
  - section state is tracked per column.
- **Sections:** a short line styled as a heading (capitals, bold or larger) that names a section in other words is a
  heading, decided by the first keyword ("EARLIER EXPERIENCE", "LEADERSHIP & RECOGNITION", "EDUCATION & CERTIFICATIONS").
  It is never a line with digits, separators, a job title, a company form or a place.
- **Dates:** two months sharing one year ("Jan–Mar 2021") give both months in that year; nothing else is added.
- **Entries:** one-paragraph entries keep their description; the text after the dates begins it.
- **Employer:** a phrase describing a kind of business is never taken as the employer. With a name beside it, the name
  wins; alone, the employer stays empty and is flagged for the user.
- **Education:** courses and certificates in an education section become certifications, not institutions.
- **Completeness:** every line of the experience section is placed or reported. `unresolved` items (shown on the review
  screen, de-identified like all other text, never made into records by the parser):
  - `possible_role`: a dated entry with no title or employer, or a long dated paragraph naming a role;
  - `unattached_text`: experience text before any role;
  - `role_without_title`;
  - `employer_unclear`;
  - dated role-like lines above the first heading or in a summary.

  A summary that mentions employers without dates is not a role.

### 6.3 Evaluation split (all fictional, rendered by Trace; no real resume or personal data)

| Split | File | Use |
|---|---|---|
| Development | `corpus.py` (10 personas) | Debugging; historical results in section 4 |
| **Regression** | `regression.py` (R01–R03) | Curated gate: complete extraction required in every layout. R01 reproduces the structure of the review failure with fictional names (most recent role; "Global Product Manager" at a short-named employer with a tagline; an "Earlier Experience" section of one-paragraph entries with a shared-year range; a software-testing role; an overlapping co-founder role; "Leadership & Recognition"; "Education & Certifications"; a summary naming earlier employers). R02 covers promotions, concurrent freelance work, a gap and a role across a page break. R03 covers a legal-entity employer with a tagline, MM/YYYY dates and an earlier internship at the same employer. |
| Validation | `validation.py` (V01–V03) | Calibration; looked at while tuning |
| **Fresh held-out** | `holdout2.py` (H201–H204) | Written before the freeze and never tuned against. Same author as the parser, so it is not independent. |
| Old held-out | `holdout.py` (H01–H03) | No longer blind (section 4); kept as a regression test |

Each persona is rendered in 7 layouts: the 5 above, plus `pdf_compact` and `docx_compact`, which reproduce the failure's
structure.

**Structural metrics** (`evaluate.py`; reported per split, per layout and **per document**, never as a single score):
- employment-record precision and recall (title AND employer correct);
- missing records, and those missing **silently** (not in any `unresolved` item);
- unsupported records;
- employer substitutions;
- bullet and description attribution (in their own role and in no other);
- earlier-experience recall;
- dates, employer and title precision, skills, fabrication.

**Thresholds**, fixed on 2026-11-20 before the fresh held-out set was run:

| Metric | Regression gate | Validation / held-out |
|---|---|---|
| Record precision / recall | 1.0 / 1.0 | ≥ 0.95 / ≥ 0.90 |
| Missing-record rate | 0 | ≤ 0.10 |
| Silently missing | 0 | 0 |
| Unsupported records / employer substitutions | 0 / 0 | 0 / 0 |
| Bullet attribution | 1.0 | ≥ 0.95 |
| Earlier-experience recall | 1.0 | ≥ 0.90 |
| Date accuracy | 1.0 | ≥ 0.95 |
| Fabrication / duplicates / forbidden skills | 0 | 0 |

Skill labels are compared in the vocabulary's canonical form ("Roadmapping" is "Product roadmaps"). This is a scoring
change made with 2.1; the section 4 numbers used literal labels.

### 6.4 Results (final parser, source hash `15f9181c1fb7d09e`)

| Split | Documents | Result |
|---|---|---|
| Regression | 21 | **All gates met**: record precision and recall 1.000; 0 missing, 0 unsupported, 0 substitutions; attribution 1.000; earlier recall 1.000; dates 1.000; fabrication 0 |
| Development | 70 | All structure thresholds met (every metric 1.000; fabrication 0). The historical 2.0 table on 50 documents also passes (education 0.983, skill recall 0.975) |
| Validation | 21 | All thresholds met (every structural metric 1.000) |
| **Fresh held-out** | 28 | **Below thresholds.** Record precision 0.973 (pass), **record recall 0.869 (fail)**; missing-record rate 0.048 (pass); **silently missing 1 (fail)**; **unsupported 1 (fail)**; **employer substitutions 1 (fail)**; **attribution 0.946 (fail)**; **earlier recall 0.743 (fail)**; dates 0.988, employer precision 0.975, title precision 0.987, skills 1.000 and fabrication 0 (pass) |
| 41 synthetic fixture PDFs (another template) | 41 | No crashes; exact job count 40/41 (2.0: 39/41); start dates 105/105 (2.0: 99/105); median 10 ms |

**Held-out failures by category** (four documents). Diagnosed but **not fixed**, because fixing them would tune against
this set:

1. **Title vocabulary:** "Teaching Assistant" has no job-title word the parser knows. The role is kept and surfaced as
   `role_without_title`, not dropped, but it isn't a record until the user adds the title.
2. **An employer ending in an abbreviation** ("Example Distribution Co."): the full stop was read as the end of a
   sentence. In a grouped layout the next role took the previous employer (1 substitution). In the two-column layout the
   role was missed (the 1 silent miss).
3. **A short page with right-aligned dates** in the compact PDF layout was read as two columns, so the first role's
   header was separated from its dates.

Proposed for the next release, each with a fresh held-out set: a broader title vocabulary, abbreviation-aware sentence
ends, and a date-column check in column detection.

The first blind run was on hash `c856e20a975990bb`. One later change, prompted by an existing API test (a wrapped
sidebar institution) and not by this set, restored the 2.0 rule for continuing a paragraph (hash `ffd6267a7d2a13e7`). The last change, to comments only, replaced
examples that were too close to the reviewed resume with fictional ones (`15f9181c1fb7d09e`). The re-scored held-out result
on the final hash is identical.

### 6.5 What this means

The structure behind the review failure is extracted correctly in every layout tested, and nothing in the experience
section is dropped silently: what the parser can't place is shown to the user. The fresh held-out set shows the parser
is **not** release-ready on unseen conventions (recall 0.869). Resume upload remains a reviewed draft that the user
checks, corrects and confirms; it is not a trusted source of facts. Quality on real resumes is unmeasured until a
consented, labelled real-resume set exists.

## 7. Parser 2.2 (D49, 2026-11-22): employer-first headings and explicit record segmentation

Sections 1–6 are kept unchanged as the historical record.

### 7.1 Why a role stayed in "unplaced text" (second real review)

A role's header was `Employer (Registered Entity), Job Title Company ↗ | Feb 2026 – Oct 2026`. The causes, and the
stage each happened in:

| Cause | Stage |
|---|---|
| The hyperlink label "Company ↗" sits after the job title. The glyph was removed, the word stayed, and "Job Title Company" scored as a company rather than a title, so no title was found. | Extraction, then title detection |
| A cleaning step stripped the closing bracket of "Employer (Registered Entity)", leaving "Employer (Registered Entity". | Employer extraction |
| Parser 2.1 dropped a role with no title from the record and reported only a one-line note. **The role's description and all its bullets were lost.** | Canonical record |

The other employers in that resume (one-sentence "Earlier Experience" entries, an overlapping founder role) were already
correct.

### 7.2 Changes

- **Stage 1, `segment_experience`:** separates the experience section into employment records (header lines, a dated
  anchor or an undated role's first bullet, a one-paragraph entry's tail, and the body) using structure only. Every
  paragraph lands in exactly one of: a record, text before the first record, or a dated paragraph that may be another
  role.
- **Stage 2:** reads each record's title, employer, dates, description and bullets.
- **Untitled roles:** a role whose title can't be read is still a role. It is flagged, keeps its `source` line and its
  lines, and is never fabricated.
- **Splitting and cleaning:** header splitting ignores separators inside brackets, and cleaning keeps a bracket that has
  its partner.
- **Hyperlink labels:** a bracketed label with its glyph ("(Case Study ↗)") is removed from text. A trailing "Company"
  after a job title is dropped from that fragment. A bare "Company ↗" can end a company tagline that is itself a link,
  so it is not removed elsewhere.
- **Unplaced text** carries what it supports (title, employer, dates, description) for "Add as a role".
- **Further additions:**
  - an institution named only by its position in "Degree, Field, Institution, years, grade" (filled from the source,
    flagged for review);
  - certification lists separated by "|" and wrapped across lines;
  - taglines ending in a business noun ("… Startup") are not employers;
  - a broader job-title vocabulary;
  - abbreviation-aware sentence ends ("Co.");
  - right-aligned date columns are not sidebars;
  - a sidebar may have a bullet's hanging indent.

The last three come from the diagnosis of `holdout2.py`, which is therefore **development data now**.

### 7.3 Evaluation

New test material:
- regression fixture **R04** (fictional), the structure of the second review;
- two employer-first layouts (`pdf_employer_first`, `docx_employer_first`), so every split now runs in **9 layouts**;
- a fresh held-out set **`holdout3.py`** (4 personas, 36 documents), written before any Phase 4 parser change (hash
  `9afc0d4a1da8d1e5`) and scored once after the freeze (parser hash `0ff42d09893a2e23`; the final hash `9f90cdaba9ad9d9e` differs only by a
docstring example, and re-running gives byte-identical output).

Scoring change: the employer counts as correct when it is the name, or the name with its registered entity as written
("Novaline (Novaline Care Labs)").

| Split | Documents | Result |
|---|---|---|
| Regression (R01–R04) | 36 | All gates met: record precision and recall 1.000, 0 missing, 0 substitutions, attribution 1.000 |
| Development (`corpus.py`) | 90 | All structure thresholds met |
| Validation | 27 | All thresholds met |
| Development (`holdout2.py`, formerly held-out) | 36 | All thresholds met (after the fixes its diagnosis informed) |
| **Fresh held-out (`holdout3.py`), first and only blind run** | 36 | **All thresholds met.** Record precision and recall 0.981; missing 0; silently missing 0; unsupported 0; substitutions 0; attribution 1.000; earlier-experience recall 1.000; dates 1.000; employer precision 1.000; title precision 0.981; skills precision 0.950; fabrication 0 |
| 41 synthetic fixture PDFs | 41 | No crashes; job count 40/41; start dates 105/105; 0 unplaced items |

**Held-out failure category, reported and not fixed:** in the employer-first layouts a one-word title followed by a
"Site" link label ("Buyer Site") kept the label. The trailing-label rule applies only to titles of three or more words.

### 7.4 One real resume, checked privately

The reviewed resume is used as a **private, local check only**. It is not in the repository, any fixture, any log or
any automated test. The ground truth and the script live outside the repository; the script prints metrics only.

| Parser | Records | Title | Employer | Start | End | Description | Bullets exact | Attribution |
|---|---|---|---|---|---|---|---|---|
| 2.1 | 4 of 5 (one parked as unplaced, its lines lost) | P 1.00 / R 0.80 | 0.80 / 0.80 | 0.80 / 0.80 | 0.80 / 0.80 | 0.80 / 0.80 | 4/5 | 4/5 |
| **2.2** | **5 of 5, 0 unplaced** | **1.00 / 1.00** | **1.00 / 1.00** | **1.00 / 1.00** | **1.00 / 1.00** | **1.00 / 1.00** | **5/5** | **5/5** |

This is one resume. It shows the reported failure is fixed, not that real-resume quality is measured.

### 7.5 What this does and doesn't show

Parser quality is measured above, separately from the review. A form that lets the user correct fields does **not**
make the parser more accurate.

Confirmation reliability is tested separately (`tests/test_review_contract.py`, `tests/test_resume_import_*.py`, and a
browser run): every contract field, structured errors, atomic writes, retries without duplicates, and a failure that
keeps the draft.

Real-resume quality remains unmeasured until a consented, labelled real-resume set exists.
