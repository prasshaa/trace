"""VALIDATION personas for parser 2.1 (calibration: may be looked at while tuning; never the release estimate).
Fictional people and employers only. Variations on the structures in regression.py with different wording."""
V = []


def persona(**kw):
    V.append(kw)
    return kw


persona(
    id="V01", name="Nikhil Specimen", date_style="month_yyyy", earlier_heading="Prior Experience",
    summary="Product manager in fintech, previously at Paylark and Corvane, with a background in data analysis.",
    jobs=[
        dict(title="Product Manager", employer="Paylark", tagline="A leading fintech platform for small businesses", start="2021-03", end=None,
             description="Product manager for invoicing and collections, serving 20,000 small businesses.",
             bullets=["Launched automated payment reminders that reduced overdue invoices by 18%.",
                      "Worked with risk and engineering on a new onboarding flow, approving merchants in under a day."]),
        dict(title="Data Analyst", employer="Corvane Retail Pvt Ltd", start="2019-07", end="2021-02", earlier=True,
             description="Built weekly sales and inventory reports in SQL and Tableau for the category teams.", bullets=[]),
        dict(title="Research Intern", employer="Corvane Retail Pvt Ltd", start="2019-01", end="2019-06", shared_year=True, earlier=True,
             description="Surveyed store managers and summarised findings on stock-outs for the operations head.", bullets=[]),
    ],
    education=[dict(qualification="B.Sc", field="Statistics", institution="Fictional University of Science", end="2018")],
    certifications=[dict(title="Product Analytics Certificate", organisation="Example Learning Co", year=2022)],
    projects=[],
    skills_section=["Product discovery", "SQL", "Tableau", "Experimentation", "Roadmapping"],
    expected_skills=["Product discovery", "SQL", "Tableau", "Experimentation", "Roadmapping", "Onboarding", "Risk management"],
)

persona(
    id="V02", name="Sana Mockup", date_style="yyyy_mm", earlier_heading="Other Experience",
    jobs=[
        dict(title="Growth Marketing Manager", employer="Orion Marketing Services Pvt Ltd", tagline="Digital marketing agency",
             start="2022-04", end=None,
             bullets=["Runs paid acquisition across search and social for 12 consumer brands, with a monthly budget of 40 lakh.",
                      "Built a creative testing process that improved cost per acquisition by 22%."]),
        dict(title="Content Strategist", employer="Brightpage Media", start="2019-08", end="2022-03",
             bullets=["Planned the editorial calendar and grew newsletter subscribers from 8,000 to 50,000."]),
        dict(title="Volunteer Mentor", employer="Example Youth Foundation", start="2020-01", end="2022-12", earlier=True,
             description="Mentored first-generation college students on job applications, alongside full-time work.", bullets=[]),
    ],
    education=[dict(qualification="BA", field="English", institution="Fictional College of Arts", end="2019")],
    certifications=[], projects=[],
    skills_section=["Performance marketing", "SEO", "Google Analytics", "Content strategy", "Copywriting"],
    expected_skills=["Performance marketing", "SEO", "Google Analytics", "Content strategy", "Copywriting", "Mentoring",
                     "Growth marketing"],
)

persona(
    id="V03", name="Rahul Dummy", date_style="mon_yyyy",
    jobs=[
        dict(title="Senior Data Scientist", employer="Bluefin Analytics", tagline="Healthcare Analytics & Automation Company",
             start="2023-05", end=None,
             bullets=["Leads a forecasting model for hospital bed demand used by 25 hospitals.",
                      "Presents model performance to clinical operations every month."]),
        dict(title="Data Scientist", employer="Bluefin Analytics", start="2021-02", end="2023-04",
             bullets=["Built claims anomaly detection in Python that flagged 3% of claims for review."]),
        dict(title="Analyst", employer="Fictional Insurance Ltd", start="2017-06", end="2019-12", earlier=True,
             description="Prepared actuarial data extracts and loss reports; left in 2020 for a career break.", bullets=[]),
    ],
    education=[dict(qualification="M.Sc", field="Mathematics", institution="Example Institute of Mathematics", end="2017")],
    certifications=[], projects=[],
    skills_section=["Python", "Machine learning", "Forecasting", "SQL", "Statistics"],
    expected_skills=["Python", "Machine learning", "Forecasting", "SQL", "Statistics", "Anomaly detection"],
)
