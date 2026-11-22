"""FRESH HELD-OUT personas for parser 2.1. Written on 2026-11-20 BEFORE the parser was frozen, and scored ONCE after the
freeze (`python -m evaluation.resume_parser --splits --holdout`). Never used for tuning; a failure found here is reported,
not fixed against this set. Same author as the parser (not an independent set). Fictional people and employers only.
Conventions differ from regression.py and validation.py on purpose: other heading words for earlier roles, taglines in
other forms, "Sept" spellings, concurrent founder work, and a career-history heading."""
H2 = []


def persona(**kw):
    H2.append(kw)
    return kw


persona(
    id="H201", name="Tara Unknown", date_style="mon_yyyy", earlier_heading="Previous Roles",
    jobs=[
        dict(title="Lead Product Designer", employer="Kilnworks", tagline="Design Systems & Developer Tools Startup", start="2023-08", end=None,
             bullets=["Owns the design system used by six product teams and 40 engineers.",
                      "Ran usability studies with 30 developers that shaped the new onboarding."]),
        dict(title="Product Designer", employer="Marigold Learning", start="2020-09", end="2023-07",
             bullets=["Designed the mobile course player used by 200,000 learners."]),
        dict(title="UX Intern", employer="Marigold Learning", start="2020-01", end="2020-06", shared_year=True, earlier=True,
             description="Created wireframes and ran guerrilla usability tests for the teacher dashboard.", bullets=[]),
    ],
    education=[dict(qualification="B.Des", field="Interaction Design", institution="Fictional School of Design", end="2020")],
    certifications=[], projects=[],
    skills_section=["Figma", "Design systems", "Usability testing", "Prototyping", "User research"],
    expected_skills=["Figma", "Design systems", "Usability testing", "Prototyping", "User research", "Wireframing"],
)

persona(
    id="H202", name="Omar Holdout", date_style="month_yyyy",
    jobs=[
        dict(title="Operations Manager", employer="Zentro Logistics Pvt Ltd", tagline="Supply chain SaaS and fulfilment services",
             start="2022-01", end=None,
             bullets=["Runs fulfilment for 3 warehouses shipping 15,000 orders a day.",
                      "Cut late shipments from 9% to 3% by redesigning pick-and-pack shifts."]),
        dict(title="Co-Founder", employer="Thaliwala", start="2021-06", end=None,
             bullets=["Co-founded a weekend home-food delivery service, run alongside the full-time role."]),
        dict(title="Shift Supervisor", employer="Example Distribution Co.", start="2018-04", end="2021-12", earlier=True,
             description="Supervised 25 associates across inbound and returns, and trained new hires on safety.", bullets=[]),
    ],
    education=[dict(qualification="MBA", field="Operations", institution="Fictional Institute of Management", end="2018")],
    certifications=[dict(title="Lean Six Sigma Green Belt Certificate", organisation="Example Quality Council", year=2020)],
    projects=[],
    skills_section=["Operations management", "Lean", "Excel", "Inventory management", "Team leadership"],
    expected_skills=["Operations management", "Lean", "Excel", "Inventory management", "Team leadership", "Training"],
)

persona(
    id="H203", name="Lina Blind", date_style="mon_yyyy", earlier_heading="Earlier Career",
    jobs=[
        dict(title="Associate Product Manager", employer="Ferncast", start="2024-02", end=None,
             description="APM on the creator payouts team.",
             bullets=["Shipped instant payouts to 9,000 creators with the payments team."]),
        dict(title="Customer Success Associate", employer="Ferncast", start="2022-07", end="2024-01",
             bullets=["Handled escalations for the top 50 creator accounts and wrote the support playbook."]),
        dict(title="Teaching Assistant", employer="Fictional University", start="2020-09", end="2020-12", shared_year=True, earlier=True,
             description="Ran tutorials for the introductory economics course.", bullets=[]),
    ],
    education=[dict(qualification="BA", field="Economics", institution="Fictional University", end="2021")],
    certifications=[], projects=[],
    skills_section=["Customer success", "SQL", "Product discovery", "Stakeholder management"],
    expected_skills=["Customer success", "SQL", "Product discovery", "Stakeholder management", "Payments"],
)

persona(
    id="H204", name="Vikram Unseen", date_style="mm_yyyy", earlier_heading="Career History",
    jobs=[
        dict(title="Principal Engineer", employer="Stonegate Cloud", tagline="Cloud Infrastructure & Security Platform", start="2021-10", end=None,
             bullets=["Sets the technical direction for the identity platform serving 4 million users.",
                      "Led the zero-downtime migration of authentication to a new region."]),
        dict(title="Staff Engineer", employer="Quartzline Technologies", start="2017-03", end="2021-09", earlier=True,
             description="Built the access-control service and mentored a team of six engineers.", bullets=[]),
        dict(title="Software Engineer", employer="Quartzline Technologies", start="2014-07", end="2017-02", earlier=True,
             description="Developed REST APIs for the billing system in Java.", bullets=[]),
    ],
    education=[dict(qualification="B.Tech", field="Computer Science", institution="Example Institute of Technology", end="2014")],
    certifications=[], projects=[],
    skills_section=["Java", "Go", "Kubernetes", "Distributed systems", "Security"],
    expected_skills=["Java", "Go", "Kubernetes", "Distributed systems", "Security", "REST APIs", "Mentoring"],
)
