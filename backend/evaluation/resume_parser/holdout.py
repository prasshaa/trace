"""HELD-OUT personas: written after the parser was tuned on corpus.py and never used for tuning. Scored once to estimate
real-world quality honestly. Fictional people and employers only. Conventions deliberately differ from corpus.py:
"Since"/"to"/"till" date phrasing, a promotion block, an internship, concurrent part-time work, a career gap,
designations with "Sr."/"Mgr", certifications with years in brackets, and long multi-line bullets.
"""
H = []


def persona(**kw):
    H.append(kw)
    return kw


persona(
    id="H01", name="Farah Unseen", date_style="mon_yyyy",
    jobs=[
        dict(title="Sr. Data Scientist", employer="Indigo Retail Analytics Pvt Ltd", location="Bengaluru", start="2022-06", end=None,
             bullets=["Built a churn model with gradient boosting that the retention team now uses to target 40,000 customers every month, and presented the results to category heads.",
                      "Ran experiments on recommendation ranking with the product team and reported uplift weekly."]),
        dict(title="Data Scientist", employer="Indigo Retail Analytics Pvt Ltd", location="Bengaluru", start="2020-01", end="2022-05",
             bullets=["Developed NLP pipelines in Python to classify customer reviews into 30 themes."]),
        dict(title="Analytics Intern", employer="Saffron Insights", location="Pune", start="2019-06", end="2019-12",
             bullets=["Cleaned survey data and built Excel summaries for the research team."]),
    ],
    education=[dict(qualification="M.Tech", field="Data Science", institution="Fictional Institute of Science", end="2019")],
    skills_section=["Python", "Machine learning", "NLP", "SQL", "Spark", "Statistics"],
    certifications=[dict(title="TensorFlow Developer Certificate", organisation="Fictional Learning Org", year=2021)],
    projects=[],
    expected_skills=["Python", "Machine learning", "Natural language processing", "SQL", "Spark", "Statistics", "Excel"],
)

persona(
    id="H02", name="Gautam Heldout", date_style="mm_yyyy",
    jobs=[
        dict(title="Operations Mgr", employer="Banyan Foods Ltd", location="Delhi", start="2021-03", end=None,
             bullets=["Run daily operations for 14 cloud kitchens and own food cost, delivery time and hygiene audits.",
                      "Introduced a vendor scorecard that cut late deliveries from suppliers by a fifth."]),
        dict(title="Weekend Volunteer Coordinator", employer="Example Food Bank Foundation", location="Delhi", start="2021-06", end=None, concurrent=True,
             bullets=["Schedule 30 volunteers for weekend distribution drives."]),
        dict(title="Shift Supervisor", employer="Banyan Foods Ltd", location="Delhi", start="2017-02", end="2019-08",
             bullets=["Supervised a team of 12 in a central kitchen."]),
    ],
    education=[dict(qualification="BBA", field=None, institution="Fictional College of Management Studies", end="2016")],
    skills_section=["Operations management", "Vendor management", "Excel", "Team leadership"],
    certifications=[], projects=[],
    expected_skills=["Operations management", "Vendor management", "Excel", "Team leadership"],
)

persona(
    id="H03", name="Leela Novel", date_style="month_yyyy",
    jobs=[
        dict(title="Product Owner", employer="Copperleaf Insurance Technologies", location="Mumbai", start="2021-10", end=None,
             bullets=["Own the backlog for the claims mobile app and write user stories with acceptance criteria for two Scrum teams.",
                      "Partnered with underwriting and compliance to launch instant claim settlement for small claims, reducing settlement time from 9 days to 2."]),
        dict(title="Business Analyst", employer="Example Insurance Ltd", location="Mumbai", start="2018-08", end="2021-09",
             bullets=["Documented requirements for the policy administration system and supported user acceptance testing."]),
    ],
    education=[dict(qualification="B.Com", field=None, institution="Fictional University of Commerce", end="2018")],
    skills_section=["Scrum", "User stories", "Jira", "Requirements gathering", "SQL"],
    certifications=[dict(title="Certified Scrum Product Owner", organisation="Fictional Agile Alliance", year=2022)],
    projects=[],
    expected_skills=["Scrum", "User stories", "Jira", "Requirements gathering", "SQL"],
)
