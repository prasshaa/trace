"""FRESH HELD-OUT personas for the Phase 4 parser (2026-11-22). Written BEFORE any Phase 4 parser change and scored ONCE
after the freeze (`python -m evaluation.resume_parser --splits --holdout3`). Never used for tuning; failures are reported,
not fixed against this set. Same author as the parser (not independent). Fictional people and employers only.

holdout2.py was scored blind once (2026-11-20) and its failures were then diagnosed; it is now a DEVELOPMENT set.
Conventions here: employer-first and title-first headings, registered-entity names, hyperlink labels ("Website", "Site",
"Company"), earlier roles as sentences under other heading words, "Since"/"Present" spellings, a concurrent side role,
institutions without a keyword, and certifications as a wrapped "|" list.
"""
H3 = []


def persona(**kw):
    H3.append(kw)
    return kw


persona(
    id="H301", name="Anaya Heldout", date_style="mon_yyyy", earlier_heading="Previous Experience",
    jobs=[
        dict(title="Category Manager", employer="Fernway", entity="Fernway Retail Pvt Ltd", link_label="Website", start="2023-03", end=None,
             description="Category manager for home and kitchen, owning assortment, pricing and vendor terms for 1,400 products.",
             bullets=["Renegotiated terms with 30 vendors, improving category margin by 3.2 points.",
                      "Launched a private-label cookware range that reached 11% of category revenue in its first year."]),
        dict(title="Buyer", employer="Marlow Stores", link_label="Site", start="2019-07", end="2023-02",
             description="Buyer for seasonal apparel across 40 stores, planning open-to-buy budgets with merchandising.",
             bullets=["Cut end-of-season markdowns by 18% with a weekly sell-through review."]),
        dict(title="Merchandising Trainee", employer="Marlow Stores", start="2018-07", end="2019-06", earlier=True,
             description="Rotated through planning, allocation and visual merchandising teams.", bullets=[]),
    ],
    education=[dict(qualification="BBA", field="Marketing", institution="Oakridge Business College", start="2015", end="2018")],
    certifications=[dict(title="Retail Analytics", organisation="Example Learning Co", year=2022, span="03/2022 – 06/2022"),
                    dict(title="Negotiation Fundamentals", organisation="Fictional Academy", year=2021, span="2021")],
    projects=[],
    skills_section=["Category management", "Pricing", "Vendor management", "Excel", "Forecasting"],
    expected_skills=["Category management", "Pricing", "Vendor management", "Excel", "Forecasting"],
)

persona(
    id="H302", name="Rohan Unseen", date_style="mm_yyyy",
    jobs=[
        dict(title="Machine Learning Engineer", employer="Stellate", entity="Stellate AI Technologies", link_label="Company",
             start="2022-05", end=None,
             description="ML engineer on the document understanding team, shipping extraction models for insurance claims.",
             bullets=["Trained a layout-aware extraction model that cut manual claim review by a quarter.",
                      "Built the offline evaluation harness used to gate every model release."]),
        dict(title="Data Scientist", employer="Quillmark Analytics", start="2019-08", end="2022-04",
             description="Data scientist building churn and propensity models for telecom clients.",
             bullets=["Delivered a churn model adopted by two operators for monthly retention campaigns."]),
        dict(title="Founder", employer="Paperkite Tutors", start="2020-06", end="2021-12", earlier=True,
             description="Ran an online maths tutoring service for 60 school students alongside full-time work.", bullets=[]),
    ],
    education=[dict(qualification="M.Tech", field="Computer Science", institution="Riverbend Engineering Academy", end="2019",
                    grade="8.4 CGPA")],
    certifications=[], projects=[],
    skills_section=["Python", "PyTorch", "Machine learning", "SQL", "MLOps"],
    expected_skills=["Python", "PyTorch", "Machine learning", "SQL", "MLOps"],
)

persona(
    id="H303", name="Meher Blindset", date_style="month_yyyy", earlier_heading="Other Experience",
    jobs=[
        dict(title="Program Manager", employer="Cobalt Health", start="2024-01", end=None,
             description="Program manager for the clinic onboarding programme across three states.",
             bullets=["Brought 120 clinics live on the scheduling platform in nine months."]),
        dict(title="Operations Lead", employer="Cobalt Health", start="2021-04", end="2023-12",
             description="Led patient-support operations for the telehealth product.",
             bullets=["Reduced average ticket resolution time from 30 hours to 9 hours."]),
        dict(title="Research Assistant", employer="Fictional Institute of Public Health", start="2020-02", end="2020-11",
             shared_year=True, earlier=True, description="Cleaned survey data and drafted literature reviews for a nutrition study.",
             bullets=[]),
    ],
    education=[dict(qualification="MPH", field=None, institution="Fictional Institute of Public Health", end="2020"),
               dict(qualification="B.Sc", field="Biology", institution="Lakeview College", end="2018")],
    certifications=[dict(title="PMP Certification", organisation="Example Project Institute", year=2023, span="2023")],
    projects=[],
    skills_section=["Program management", "Stakeholder management", "Operations", "Process improvement"],
    expected_skills=["Program management", "Stakeholder management", "Operations", "Process improvement"],
)

persona(
    id="H304", name="Kabir Testcase", date_style="mon_apos",
    jobs=[
        dict(title="Senior Backend Engineer", employer="Driftwood Payments", entity="Driftwood Fintech Pvt. Ltd.", link_label="Company",
             start="2023-09", end=None,
             description="Backend engineer on the settlements platform, owning the reconciliation service.",
             bullets=["Rewrote the reconciliation jobs in Go, cutting nightly run time from four hours to forty minutes.",
                      "Mentored three engineers and ran the team's on-call rotation."]),
        dict(title="Software Engineer", employer="Driftwood Payments", entity="Driftwood Fintech Pvt. Ltd.", start="2021-01",
             end="2023-08", description="Built merchant onboarding APIs and the KYC document pipeline.",
             bullets=["Shipped the self-serve onboarding API used by 9,000 merchants."]),
        dict(title="Software Engineer Intern", employer="Tidewater Labs", start="2020-01", end="2020-06", earlier=True,
             description="Wrote integration tests and fixed bugs in the billing service.", bullets=[]),
    ],
    education=[dict(qualification="B.E.", field="Information Technology", institution="Harbourline Polytechnic", end="2020")],
    certifications=[], projects=[],
    skills_section=["Go", "Java", "PostgreSQL", "Kubernetes", "System design"],
    expected_skills=["Go", "Java", "PostgreSQL", "Kubernetes", "System design"],
)
