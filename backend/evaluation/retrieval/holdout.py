"""HELD-OUT cases for the retrieval evaluation. SYNTHETIC (invented people, products and numbers).

Written 2026-11-23, BEFORE any semantic or hybrid retriever existed, by the same author as tuning.py (so not
independent; see cases.py). Rules: never read while tuning; score a method only after its configuration is frozen; record
every holdout run in docs/RETRIEVAL_EVALUATION.md with the method version. Once a method has been changed because of a
holdout result, this set is no longer blind for that method and a fresh one must be written.
"""
from evaluation.retrieval.cases import (AI, BC, CA, DA, DE, DG, DR, EC, GM, OO, OP, PB, PD, PL, QT, RA, RO, SM, SP, TE,
                                         Case, R)

H = (
    Case("H01", "Business analyst at an insurer", records=(
        R("H01-1", "Ran workshops with claims handlers to capture how the claims process really works.", clear=(RA,),
          related=(PD, SM), tags=("synonym", "non_pm_background")),
        R("H01-2", "Turned the workshop output into user stories the developers could pick up.", clear=(RA,), related=(PB,),
          tags=("wording",)),
        R("H01-3", "Agreed the order of the claims-portal changes with the product owner.", related=(PB, SM),
          tags=("implicit_ownership",)),
        R("H01-4", "Checked that each release matched the agreed stories before sign-off.", clear=(QT,), related=(RA, DE)),
        R("H01-5", "Kept underwriting, compliance and IT aligned on the new consent wording.", clear=(SM,), related=(DG,),
          tags=("synonym",)),
        R("H01-6", "Attended a two-day course on agile product ownership.", kind="course", tags=("course", "keyword_overlap")),
        R("H01-7", "Owned the claims roadmap.", clear=(RO,), status="pending", tags=("ineligible",)),
    )),
    Case("H02", "Teacher moving into edtech product work", records=(
        R("H02-1", "Taught maths to classes of 30 students for six years.", tags=("non_pm_background",)),
        R("H02-2", "Piloted a homework app with my classes and reported what confused students to the vendor.",
          related=(PD, DR), tags=("implicit_ownership", "wording")),
        R("H02-3", "Led the department's switch to a new grading platform, training 12 colleagues.", clear=(DE, PL),
          related=(SM, OP), tags=("wording",)),
        R("H02-4", "Analysed term results to find which topics students struggled with.", clear=(DA,)),
        R("H02-5", "Read widely about product strategy and roadmaps.", tags=("keyword_overlap", "negative")),
    )),
    Case("H03", "Product manager at a marketplace", records=(
        R("H03-1", "Chose what the seller-tools team worked on each quarter after weighing seller interviews and revenue impact.",
          clear=(PB, RO), related=(PD, BC), tags=("synonym", "explicit_ownership")),
        R("H03-2", "Listened in on seller support calls every week to spot problems worth solving.", clear=(PD,),
          tags=("wording",)),
        R("H03-3", "Took the bulk-listing tool from idea to launch with two engineers and a designer.", clear=(DE,),
          related=(SM, PD), tags=("wording", "explicit_ownership")),
        R("H03-4", "Set listings-per-seller as the team's success measure and used it to drop two planned features.",
          clear=(OO, PB), related=(DA,), tags=("explicit_ownership",)),
        R("H03-5", "Briefed the marketing team so the launch email went out on release day.", related=(GM, SM)),
        R("H03-6", "Was not responsible for pricing decisions.", tags=("negative", "keyword_overlap")),
        R("H03-7", "Owned the seller-tools roadmap until 2021.", clear=(RO,), job="J2", status="superseded",
          tags=("ineligible", "outdated")),
    )),
    Case("H04", "Support team lead at a telecom", records=(
        R("H04-1", "Managed a team of eight support agents across two shifts.", clear=(PL,), related=(OP,),
          tags=("synonym",)),
        R("H04-2", "Rewrote the escalation playbook, halving repeat contacts.", clear=(OP,), related=(CA, OO)),
        R("H04-3", "Collected the top ten customer complaints each month and shared them with product.", related=(PD, CA),
          tags=("implicit_ownership", "non_pm_background")),
        R("H04-4", "Handled escalations from enterprise customers personally.", clear=(CA,)),
        R("H04-5", "Collaborated with engineering on outage communications.", related=(SM,),
          tags=("keyword_overlap", "implicit_ownership")),
    )),
    Case("H05", "Data engineer with an AI side role", records=(
        R("H05-1", "Built the pipelines that load billing data into the warehouse.", clear=(TE,), related=(DG,)),
        R("H05-2", "Wrote the shared definition of an 'active subscriber' and got finance to adopt it.", clear=(DG,),
          related=(SM, DA), tags=("synonym",)),
        R("H05-3", "Part-time: compared two language models on cost, latency and answer quality for an internal assistant.",
          clear=(AI,), related=(DA, TE), job="J2", tags=("overlapping_roles", "wording")),
        R("H05-4", "Worked closely with product managers.", related=(SM,), tags=("keyword_overlap", "sparse")),
        R("H05-5", "Defined access controls for customer data in the warehouse.", clear=(DG,), related=(TE,)),
    )),
    Case("H06", "Head of product (seniority)", records=(
        R("H06-1", "Decided how we split investment between the consumer app and the API business.", clear=(SP,),
          related=(BC, RO), tags=("seniority", "synonym")),
        R("H06-2", "Grew a product team from three to eleven, including two group PMs.", clear=(PL,), tags=("seniority",)),
        R("H06-3", "Took the annual plan to the board and defended the trade-offs.", clear=(EC,), related=(SP,),
          tags=("seniority",)),
        R("H06-4", "Accountable for net revenue retention across the platform.", clear=(OO,), related=(BC,),
          tags=("explicit_ownership",)),
        R("H06-5", "Sequenced a migration that touched seven teams without a missed date.", clear=(DE,), related=(SM,),
          tags=("seniority", "wording")),
    )),
    Case("H07", "Sparse resume, sales executive", records=(
        R("H07-1", "Sales executive, SME segment.", tags=("sparse",)),
        R("H07-2", "Beat quota three years running.", related=(GM,), tags=("sparse",)),
        R("H07-3", "Demoed the product to prospects and passed their feature requests to the product team.", related=(GM, PD, CA),
          tags=("sparse", "implicit_ownership")),
    )),
    Case("H08", "UX designer moving into product management", records=(
        R("H08-1", "Ran moderated usability sessions on the new booking flow and fixed the top five issues.", clear=(DR,),
          related=(PD,), tags=("synonym",)),
        R("H08-2", "Shaped what we built next by bringing research findings to sprint planning.", related=(PD, PB),
          tags=("implicit_ownership", "wording")),
        R("H08-3", "Built the design system used across three apps.", clear=(DR,), related=(TE,)),
        R("H08-4", "Measured task completion before and after the redesign: up from 61% to 84%.", clear=(DA,),
          related=(OO,), tags=("wording",)),
        R("H08-5", "Explained the case for the redesign to the CPO and got it funded.", clear=(EC,), related=(BC,)),
        R("H08-6", "Defined the product vision for the booking flow.", clear=(RO,), status="rejected",
          tags=("ineligible", "conflicting")),
    )),
)
