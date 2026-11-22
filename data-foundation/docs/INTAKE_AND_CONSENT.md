# Trace participant intake and consent draft

> Product-design draft only. Not legal advice and not ready to deploy without review by Indian privacy counsel.

## Participant intake

1. Confirm the contributor is the person named in the resume and is at least 18 years old.
2. Explain Trace's purpose: helping a working professional explore pathways to APM, PM, Senior PM or Director of Product roles.
3. Let the person upload their resume or enter their career history manually.
4. Explain that extraction may be performed by an external LLM/resume-processing provider, identifying the provider and processing location before transmission.
5. Extract roles, employers, dates, responsibilities, achievements, education and skills.
6. Show the extracted profile and ask the participant to correct it. Do not infer missing dates, titles or achievements.
7. Ask follow-up questions only where they materially affect role mapping or the career sequence.
8. Ask separate, optional consent questions below. No checkbox is pre-selected.

## Separate consent choices

### A. Process my resume for my own Trace session

I authorise Trace to process the resume and information I provide to generate my career profile and recommendations for my use. I understand that the original resume may be processed by the disclosed service providers. I can request deletion of the resume and extracted profile.

Choice: Yes / No.

### B. Contribute a de-identified career history to Trace's shared reference dataset (optional)

I separately agree that Trace may retain a de-identified version of my career history, employment dates, role transitions, broad industry and relevant experience evidence for aggregate pathway analysis and product improvement. Direct identifiers and original resume files will not be included in the reference dataset. I understand that combinations of career details may still be re-identifying and that Trace will apply safeguards and access controls.

Choice: Yes / No.

### C. Use my de-identified career history in an external demo or case study (optional)

I separately agree that Trace may use a de-identified summary of my career history in external product demonstrations, research presentations or case studies. No original resume, name, contact details or direct profile link will be shared.

Choice: Yes / No.

### D. Process my resume using named AI/model providers (optional disclosure/acknowledgement)

Trace will disclose the names of its resume parsing and model providers, categories of data sent, processing location, retention settings and any available no-training / zero-retention controls before use.

Acknowledgement: I have read the provider disclosure.

## Participant correction and withdrawal

- Participants can review and correct extracted information before it enters any reference dataset.
- Withdrawal of shared-dataset consent must stop future use and remove the record from future cohort calculations. The product must define whether previously generated aggregate statistics can be recomputed and how backups are handled.
- External-demo consent can be withdrawn separately; published material must be removed where reasonably controllable.
- Do not promise full anonymity. Use the term de-identified and explain residual re-identification risk.
- Retain original resumes only for a disclosed, limited period. Recommended prototype default: delete within 30 days after successful extraction and participant confirmation unless the participant requests earlier deletion or a different lawful basis and retention schedule is documented.
- Keep access logs, data-source records, consent version, consent timestamp and withdrawal state.

## Data handling checklist

- Private Supabase Storage bucket; no public resume URLs.
- PII stays out of the analytical reference tables.
- Candidate-specific profiles are accessible only to the authenticated owner and authorised backend services.
- Reference cohort tables are service-role/backend only, not directly exposed to browser clients.
- No use for model training unless separately and explicitly disclosed and consented.
- Do not scrape LinkedIn or other platforms contrary to their terms.
- Review applicable DPDP Act / Rules obligations, cross-border processing and processor contracts with counsel before launch.

## How consent is recorded (Data Foundation v2)

- **One event per decision.** Each choice above is stored as a row in `public.trace_consent_events` (`user_id`, `purpose`, `status` = granted, denied or withdrawn, `consent_version`, `recorded_at`). The log is append-only: a change of mind is a new event, never an edit.
- **Mapping of choices to purposes:**

  | Choice | Purpose |
  |---|---|
  | A | `resume_processing` |
  | B | `deidentified_shared_dataset` |
  | C | `external_demo` |
  | D | `model_provider_processing` |

  `product_research` is reserved for a future, separately worded choice.
- **Consent documents.** Every event references a `public.trace_consent_documents` row with the exact wording's SHA-256. A grant only counts when that document's `legal_review_status` is `approved`. **No consent document is seeded.** These drafts must be reviewed by Indian privacy counsel, and then inserted with `approved` status, before any analytics consent can take effect.
- **Effect on analytics.** A contributed reference profile links to the contributor (`contributor_user_id`) and to the grant event it was created under (`source_consent_event_id`). `trace_ref.v_eligible_profiles` re-checks the *latest* `deidentified_shared_dataset` event on every query, so withdrawal applies immediately.
- **Demo use is separate.** It needs both analytics eligibility and an active `external_demo` grant (`trace_ref.v_demo_eligible_profiles`).
- **Never inferred.** No consent is inferred from a resume upload, and no seed file may contain consent events or contributor ids. The validator enforces this.

### Open policy decisions (record the answers before launch)

1. After withdrawal of `deidentified_shared_dataset`: should the de-identified profile be deleted, or kept but permanently ineligible?
2. How should previously published aggregates and backups be handled after withdrawal?
3. Does a new consent-document version require re-consent from existing contributors?
4. What retention period applies to the de-identified reference profile itself?
