"""REGRESSION fixtures for parser 2.1: curated, deterministic, FICTIONAL people and employers only.

R01 reproduces the STRUCTURE of the 2026-11-20 review failure, never its content (all names, dates, wording and figures
are invented): a most recent job; a distinct "Global Product Manager" role at an employer with a short proper name whose
line also carries a tagline describing the business; a separate "Earlier Experience" section of one-paragraph entries
(an Associate Product Manager role with a shared-year range, a software-testing role, an overlapping co-founder role); a
"Leadership & Recognition" section;
education and certifications in one section; a summary naming earlier employers. Every clearly structured role must be
extracted, with its own employer, dates, description and bullets (gate: complete extraction, no substitutions).
R02 covers promotions under one employer, concurrent freelance work, a career gap and a long role spanning a page break.
R03 covers a legal-entity employer with a tagline, MM/YYYY dates and an earlier internship at the same employer.
R04 (Phase 4, 2026-11-22) reproduces the STRUCTURE of a second real review, with invented content: employer-first
headings with a registered-entity name in brackets and a hyperlink label after the title ("Employer (Entity), Title
Company ↗ | dates"), a descriptor after the title on the next role, a description and wrapped bullets under each, an
"Earlier Experience" section of one-sentence entries (shared-year range, a dash without spaces, an overlapping founder
role), an institution without a keyword, a grade, and certifications as one wrapped "|" list. Rendered in the employer-
first layouts as well (render.STRUCTURE_LAYOUTS).
"""
R = []


def persona(**kw):
    R.append(kw)
    return kw


persona(
    id="R01", name="Kavya Fixture", date_style="mon_yyyy",
    summary=("Product manager with nine years across logistics software and payments, previously at Vexa and Tallyfox, "
             "and earlier in software testing."),
    jobs=[
        dict(title="Senior Product Manager", employer="Quillon Freight", tagline="Fleet Operations & Logistics Software Company",
             start="2025-06", end=None,
             bullets=["Owns the route-planning roadmap for a dispatch product used by 120 regional carriers.",
                      "Shortened driver onboarding from five days to two by launching a guided setup flow with operations."]),
        dict(title="Global Product Manager", employer="Vexa", tagline="Payments Platform & Fintech Company", start="2021-04", end="2025-05",
             description=("Cross-border payments company. Product manager for merchant payouts, reconciliation and settlement "
                          "reporting across 7 markets."),
             bullets=["Set the payouts strategy and roadmap with finance, risk and engineering leads.",
                      "Led two squads (14 engineers) through a rebuild of the settlement ledger.",
                      "Cut reconciliation exceptions by 42% with automated matching rules and an exceptions queue.",
                      "Added 6 local payout rails, opening two new markets for merchants."]),
        dict(title="Associate Product Manager", employer="Tallyfox", start="2021-01", end="2021-03", shared_year=True, earlier=True,
             description=("Ran a pricing-page experiment programme for a subscription app, writing specs and analysing results "
                          "with the growth and design teams."),
             bullets=[]),
        dict(title="Software Test Engineer", employer="Brightline Technology Solutions", start="2017-08", end="2020-12", earlier=True,
             description=("Wrote and ran regression suites for an insurance claims portal, triaged production issues and kept "
                          "the release checklist for a team of nine developers."),
             bullets=[]),
        dict(title="Co-Founder & Writer", employer="Inkpot Stories", start="2016-03", end="2018-10", earlier=True,
             description=("Started a regional travel newsletter that reached 8,000 subscribers, and wrote and edited weekly "
                          "features with two volunteer photographers."),
             bullets=[]),
    ],
    leadership=[dict(title="Accessibility Guild Lead", organisation="Vexa",
                     description="Ran monthly accessibility reviews and wrote the guild's checklist for new product features.")],
    education=[dict(qualification="B.Sc", field="Physics", institution="Fictional University of Science", end="2016")],
    certifications=[dict(title="Agile Product Owner Certificate", organisation="Fictional Product Academy", year=2022)],
    projects=[],
    skills_section=["Product strategy", "Roadmapping", "SQL", "A/B testing", "Stakeholder management", "Agile"],
    expected_skills=["Product strategy", "Roadmapping", "SQL", "A/B testing", "Stakeholder management", "Agile",
                     "Regression testing", "Experimentation"],
)

persona(
    id="R02", name="Dev Sample", date_style="mon_apos",
    jobs=[
        dict(title="Engineering Manager", employer="Copperleaf Systems Pvt Ltd", start="2023-01", end=None,
             bullets=["Manages a team of nine engineers building the inventory planning platform used by 300 retail stores across four states.",
                      "Introduced quarterly planning with product and design, reducing unplanned work from 40% to 15% of each sprint.",
                      "Hired five engineers and set up an onboarding programme that halved the time to a first production change.",
                      "Led the move from nightly batch jobs to event-driven updates, so store stock levels refresh within two minutes.",
                      "Runs monthly reliability reviews; availability of the planning service rose from 99.2% to 99.9% in a year.",
                      "Partnered with finance on cloud cost reviews that cut monthly infrastructure spend by 22%."]),
        dict(title="Senior Software Engineer", employer="Copperleaf Systems Pvt Ltd", start="2020-06", end="2022-12",
             bullets=["Designed the forecasting service in Python and PostgreSQL that replaced spreadsheet-based ordering.",
                      "Mentored three junior engineers and ran fortnightly code reviews."]),
        dict(title="Freelance Data Consultant", employer="Self-employed", start="2021-01", end="2022-06",
             bullets=["Built sales dashboards in Power BI for two small distributors, alongside the full-time role."]),
        dict(title="Software Engineer", employer="Northwind Retail Ltd", start="2016-07", end="2019-03", earlier=True,
             description="Built order management features in Java for the e-commerce team and supported weekly releases.", bullets=[]),
        dict(title="Graduate Engineer Trainee", employer="Fictional Steel Works Limited", start="2015-07", end="2016-06", earlier=True,
             description="Rotated through plant automation and IT, and wrote reporting scripts for the maintenance team.", bullets=[]),
    ],
    education=[dict(qualification="B.E.", field="Electronics", institution="Example College of Engineering", end="2015")],
    certifications=[], projects=[],
    skills_section=["Python", "Java", "PostgreSQL", "Power BI", "People management", "System design"],
    expected_skills=["Python", "Java", "PostgreSQL", "Power BI", "People management", "System design", "Mentoring", "Hiring",
                     "Code review", "Forecasting", "Dashboards", "Automation"],
)

persona(
    id="R03", name="Isha Template", date_style="mm_yyyy", earlier_heading="Previous Experience",
    jobs=[
        dict(title="Product Owner", employer="Saltmarsh Software Services Pvt Ltd", tagline="B2B SaaS for logistics companies",
             start="2021-11", end=None,
             description="Product owner for the carrier booking product used by 1,200 freight forwarders.",
             bullets=["Wrote and prioritised the backlog with two engineering squads, releasing every two weeks.",
                      "Interviewed 40 dispatchers to redesign the booking flow, cutting booking time by a third."]),
        dict(title="Business Analyst", employer="Fernhill Consulting", start="2019-03", end="2021-10",
             bullets=["Documented requirements for warehouse management rollouts at three logistics clients."]),
        dict(title="Analyst Intern", employer="Fernhill Consulting", start="2018-06", end="2018-12", shared_year=True, earlier=True,
             description="Prepared market sizing models and client presentations for the logistics practice.", bullets=[]),
    ],
    education=[dict(qualification="BBA", field=None, institution="Fictional University of Commerce", end="2018")],
    certifications=[dict(title="Certified Scrum Product Owner", organisation="Example Scrum Body", year=2022)],
    projects=[],
    skills_section=["Backlog management", "User research", "SQL", "Jira", "Requirements gathering"],
    expected_skills=["Backlog management", "User research", "SQL", "Jira", "Requirements gathering", "Market sizing",
                     "Financial modelling"],
)

persona(
    id="R04", name="Ira Fixture", date_style="mon_yyyy",
    summary=("Product manager with seven years in health and logistics software. Previously led the routing product at "
             "Pallet and earlier worked in software testing."),
    jobs=[
        dict(title="Clinical AI Product Manager", employer="Novaline", entity="Novaline Care Labs", link_label="Company",
             start="2025-11", end="2026-08",
             description=("Agent platform for clinic billing teams. Sole PM for the claims-review line, working with the "
                          "founders and a four-person product team."),
             bullets=["Found a new segment in specialist clinics worth about a fifth of the market and wrote the business "
                      "case to enter it, with a pilot plan agreed with sales and clinical leads.",
                      "Compared two agent designs, a single-pass checker and a reviewer-and-arbiter pipeline, against agreed "
                      "accuracy targets, and took the second to production with engineering.",
                      "Cut manual claim checks by 9 hours a day across pilot clinics."]),
        dict(title="Routing Product Manager", employer="Pallet", tagline="Fleet Planning & Logistics Software Company",
             link_label="Company", start="2021-06", end="2025-10",
             description="Freight routing company. Product manager for route planning and dispatch across 6 regions.",
             bullets=["Owned the dispatch roadmap with operations, data and engineering leads across six regions.",
                      "Reduced empty miles by 12% with load-matching rules and a weekly planning review.",
                      "Moved the planner to a new map engine without downtime for 2,000 daily users."]),
        dict(title="Associate Product Manager", employer="Gridlark", start="2021-01", end="2021-05", shared_year=True, earlier=True,
             description="Wrote specs for a fleet-tracking dashboard and ran usability sessions with dispatchers.", bullets=[]),
        dict(title="Software Test Analyst", employer="Ember Systems", start="2018-03", end="2020-12", earlier=True,
             description="Built regression suites for a hospital billing system and coordinated weekly release testing.", bullets=[]),
        dict(title="Co-Founder & Photographer", employer="Brightlane Studio", start="2016-09", end="2018-06", earlier=True,
             description="Ran a small event-photography studio with two partners while studying, and then alongside work.",
             bullets=[]),
    ],
    leadership=[dict(title="Inclusion Network Lead", organisation="Pallet",
                     description="Organised quarterly mentoring circles for new managers across three offices.")],
    education=[dict(qualification="B.Tech", field="Mechanical Engineering", institution="Kestrel Valley Institute", start="2014",
                    end="2018", grade="8.1 CGPA")],
    certifications=[dict(title="Applied Machine Learning for PMs", organisation="Fictional Product Academy", year=2025,
                         span="02/2025 – 05/2025"),
                    dict(title="Healthcare Data Foundations", organisation="Example Learning Co", year=2024, span="03/2024"),
                    dict(title="Product Leadership Fellowship", organisation="Fictional Fellows Programme", year=2021,
                         span="01/2020 – 06/2021")],
    projects=[],
    skills_section=["Product strategy", "Roadmapping", "SQL", "A/B testing", "Stakeholder management", "User research"],
    expected_skills=["Product strategy", "Roadmapping", "SQL", "A/B testing", "Stakeholder management", "User research"],
)
