"""Controlled evaluation corpus for the resume parser: FICTIONAL people and FICTIONAL employers only.

Every persona is hand-labelled: the expected jobs (title, employer, start, end), the exact bullet texts, education,
certifications/projects and the skills a careful reader would list (expected_skills), plus fragments that must never be
accepted as skills (forbidden_skills). The same persona is rendered into several layouts (render.py), so results can be
compared by format and layout. No real resume, private profile or research record is used here. Contact details are
reserved example values (example.invalid, a 90000-series phone number).
"""

P = []


def persona(**kw):
    P.append(kw)
    return kw


persona(
    id="P01", name="Riya Fictional", date_style="mon_yyyy",
    jobs=[
        dict(title="Senior Software Engineer", employer="Nimbus Payments Pvt Ltd", location="Bengaluru", start="2021-04", end=None,
             bullets=["Designed REST APIs for the merchant settlement service handling 2 million transactions a day.",
                      "Led the migration of the payouts platform from a monolith to microservices on AWS, cutting deployment time by 60%.",
                      "Mentored four engineers and ran weekly design reviews with product and QA."]),
        dict(title="Software Engineer", employer="Kestrel Analytics", location="Pune", start="2018-07", end="2021-03",
             bullets=["Built data ingestion pipelines in Python and Kafka for retail clients.",
                      "Wrote unit tests and integration tests that raised coverage from 45% to 80%."]),
    ],
    education=[dict(qualification="B.Tech", field="Computer Science", institution="Fictional Institute of Technology", end="2018")],
    skills_section=["Python", "Java", "SQL", "AWS", "Docker", "Kafka", "System design"],
    certifications=[dict(title="AWS Certified Developer – Associate", organisation="Amazon Web Services", year=2022)],
    projects=[],
    expected_skills=["Python", "Java", "SQL", "AWS", "Docker", "Kafka", "System design", "REST APIs", "Microservices", "Mentoring",
                     "Unit testing"],
)

persona(
    id="P02", name="Arjun Example", date_style="mm_yyyy",
    jobs=[
        dict(title="Business Analyst", employer="Orbit Retail Technologies", location="Mumbai", start="2019-08", end=None,
             description="Business analyst for the loyalty and payments product line, working between merchandising, finance and engineering teams.",
             bullets=["Gathered requirements from 12 stakeholder groups and wrote user stories for the loyalty programme relaunch.",
                      "Built Power BI dashboards on redemption rates that the leadership team reviews every week.",
                      "Ran A/B tests on the checkout flow with the product manager, improving conversion by 3.5%."]),
        dict(title="Associate Consultant", employer="Meridian Consulting LLP", location="Gurugram", start="2017-06", end="2019-07",
             bullets=["Mapped order-to-cash processes for a consumer goods client and proposed process improvement changes.",
                      "Prepared financial models and presentations for steering committee meetings."]),
    ],
    education=[dict(qualification="MBA", field="Marketing", institution="Fictional School of Management", end="2017"),
               dict(qualification="B.Com", field=None, institution="Example College of Commerce", end="2015")],
    skills_section=["Requirements gathering", "SQL", "Power BI", "Excel", "Stakeholder management", "Jira"],
    certifications=[], projects=[],
    expected_skills=["Requirements gathering", "SQL", "Power BI", "Excel", "Stakeholder management", "Jira", "User stories",
                     "A/B testing", "Process improvement", "Financial modelling"],
)

persona(
    id="P03", name="Meera Placeholder", date_style="mon_apos",
    jobs=[
        dict(title="Product Manager", employer="Lumen Health Technologies", location="Hyderabad", start="2022-02", end=None,
             bullets=["Owned the product roadmap for the patient booking app used by 300 clinics.",
                      "Ran customer interviews and usability testing sessions that shaped the new onboarding flow, lifting activation by 18%.",
                      "Wrote PRDs and prioritised the backlog with engineering and design in two-week Scrum sprints."]),
        dict(title="Associate Product Manager", employer="Lumen Health Technologies", location="Hyderabad", start="2020-07", end="2022-01",
             bullets=["Analysed funnel data in Mixpanel and SQL to find drop-offs in appointment booking.",
                      "Coordinated releases with QA and support teams."]),
        dict(title="Product Analyst", employer="Vertex Commerce", location="Bengaluru", start="2019-01", end="2020-06",
             bullets=["Built weekly Tableau dashboards for category managers."]),
    ],
    education=[dict(qualification="B.E.", field="Electronics", institution="Fictional University of Engineering", end="2018")],
    skills_section=["Product roadmaps", "User research", "SQL", "Mixpanel", "Figma", "Jira", "Metrics (precision, recall, F1)"],
    certifications=[], projects=[dict(title="Clinic waitlist predictor", description="A side project that forecasts no-shows from booking history.")],
    expected_skills=["Product roadmaps", "User research", "SQL", "Mixpanel", "Figma", "Jira", "Usability testing", "PRDs",
                     "Scrum", "Tableau", "Prioritisation"],
    forbidden_skills=["F1)", "recall", "(precision", "Metrics (precision"],
)

persona(
    id="P04", name="Kabir Sample", date_style="month_yyyy",
    jobs=[
        dict(title="QA Engineer", employer="Quanta Software Solutions", location="Chennai", start="2020-01", end=None,
             bullets=["Automated regression suites with Selenium and Cypress, reducing manual test effort by 40%.",
                      "Introduced confidence-based gating for releases so that builds with flaky tests are held back automatically before production.",
                      "Worked with product owners to clarify acceptance criteria during sprint planning."]),
        dict(title="Software Test Intern", employer="Quanta Software Solutions", location="Chennai", start="2019-06", end="2019-12",
             bullets=["Executed manual testing for the billing module and logged defects in Jira."]),
    ],
    education=[dict(qualification="B.Sc", field="Computer Science", institution="Example Arts and Science College", end="2019")],
    skills_section=["Selenium", "Cypress", "Test automation", "Manual testing", "Jira", "Confidence-Based"],
    certifications=[dict(title="ISTQB Foundation Level", organisation="Fictional Testing Board", year=2020)],
    projects=[],
    expected_skills=["Selenium", "Cypress", "Test automation", "Manual testing", "Jira"],
    forbidden_skills=["Confidence-Based", "confidence-based gating"],
)

persona(
    id="P05", name="Sana Testcase", date_style="yyyy_mm",
    jobs=[
        dict(title="Data Analyst", employer="Harbour Logistics Pvt Ltd", location="Kolkata", start="2021-09", end=None,
             bullets=["Built demand forecasting models in Python that reduced stock-outs across 40 warehouses by 12%.",
                      "Maintained ETL jobs in Snowflake and documented the data model for the analytics team."]),
        dict(title="Operations Executive", employer="Harbour Logistics Pvt Ltd", location="Kolkata", start="2019-04", end="2021-08",
             bullets=["Coordinated vendor management for last-mile delivery partners in eastern India."]),
    ],
    gap=("2018-01", "2019-03"),
    education=[dict(qualification="M.Sc", field="Statistics", institution="Fictional University of Kolkata", end="2017")],
    skills_section=["Python", "Pandas", "SQL", "Snowflake", "Forecasting", "Tableau"],
    certifications=[], projects=[], volunteering=[dict(title="Data mentor, Example Learning Foundation", description="Taught spreadsheet basics to students on weekends.")],
    expected_skills=["Python", "Pandas", "SQL", "Snowflake", "Forecasting", "Tableau", "ETL", "Vendor management"],
)

persona(
    id="P06", name="Dev Illustrative", date_style="mon_yyyy",
    jobs=[
        dict(title="Co-founder", employer="Tinkerbox Labs", location="Remote", start="2023-01", end=None,
             bullets=["Built the first version of a scheduling tool for tutors and grew it to 1,200 paying users.",
                      "Ran go-to-market experiments across Instagram and school partnerships."]),
        dict(title="Growth Marketing Manager", employer="Aster Media Pvt Ltd", location="Mumbai", start="2020-03", end="2022-12",
             bullets=["Managed a performance marketing budget of Rs 2 crore a year across search and social.",
                      "Set up SEO content programmes that doubled organic sign-ups in 12 months."]),
        dict(title="Freelance Content Writer", employer="Self-employed", location=None, start="2019-01", end="2020-02", concurrent=False,
             bullets=["Wrote long-form articles for technology and finance publications."]),
    ],
    education=[dict(qualification="BBA", field=None, institution="Fictional Business College", end="2018")],
    skills_section=["Growth marketing", "Performance marketing", "SEO", "Google Analytics", "Content marketing", "Go-to-market"],
    certifications=[], projects=[],
    expected_skills=["Growth marketing", "Performance marketing", "SEO", "Google Analytics", "Content marketing", "Go-to-market"],
)

persona(
    id="P07", name="Nisha Dummy", date_style="mm_yyyy",
    jobs=[
        dict(title="Program Manager", employer="Cobalt Bank", location="Mumbai", start="2020-11", end=None,
             bullets=["Ran the program management office for the digital lending launch across six teams.",
                      "Tracked risks and dependencies weekly and reported status to the steering committee."]),
        dict(title="Project Manager", employer="Example Infotech Ltd", location="Noida", start="2016-05", end="2020-10",
             bullets=["Delivered a core banking upgrade for three regional banks on time and within budget.",
                      "Managed release management and vendor management with two outsourcing partners."]),
        dict(title="Weekend Instructor", employer="Fictional Coding Club", location="Noida", start="2018-01", end="2019-12", concurrent=True,
             bullets=["Taught introductory programming to school students."]),
    ],
    education=[dict(qualification="B.Tech", field="Information Technology", institution="Example Institute of Technology", end="2016")],
    skills_section=["Program management", "Project management", "Risk management", "Agile", "Jira", "Confluence"],
    certifications=[dict(title="PMP", organisation="Fictional Project Institute", year=2019)],
    projects=[],
    expected_skills=["Program management", "Project management", "Risk management", "Agile", "Jira", "Confluence",
                     "Release management", "Vendor management"],
)

persona(
    id="P08", name="Kiran Mock", date_style="mon_yyyy",
    jobs=[
        dict(title="UX Designer", employer="Pebble Fintech", location="Bengaluru", start="2021-06", end=None,
             bullets=["Designed the KYC onboarding flow in Figma and validated it through usability testing with 25 users.",
                      "Created wireframes and prototypes for the credit card rewards dashboard.",
                      "Partnered with product managers on discovery workshops and design thinking sessions."]),
        dict(title="UI Design Intern", employer="Example Studio", location="Pune", start="2020-12", end="2021-05",
             bullets=["Produced UI mockups for client marketing websites."]),
    ],
    education=[dict(qualification="B.Des", field="Interaction Design", institution="Fictional Institute of Design", end="2021")],
    skills_section=["Figma", "Wireframing", "Prototyping", "User research", "Usability testing", "Design thinking"],
    certifications=[], projects=[dict(title="Accessible banking app concept", description="A concept app tested with screen-reader users.")],
    expected_skills=["Figma", "Wireframing", "Prototyping", "User research", "Usability testing", "Design thinking"],
)

persona(
    id="P09", name="Tara Specimen", date_style="month_yyyy",
    jobs=[
        dict(title="Senior Product Manager", employer="Helix Mobility Pvt Ltd", location="Bengaluru", start="2022-04", end=None,
             bullets=["Own the strategy and roadmap for the driver app used by 80,000 drivers across 12 cities.",
                      "Lead a team of three product managers and work with engineering, design, operations and finance leaders on quarterly planning.",
                      "Launched dynamic pricing experiments that increased driver earnings per hour by 9% without raising rider fares, after a long period of careful testing with operations teams in two pilot cities."]),
        dict(title="Product Manager", employer="Helix Mobility Pvt Ltd", location="Bengaluru", start="2019-10", end="2022-03",
             bullets=["Shipped the in-app safety toolkit, reducing support tickets about safety by 30%."]),
        dict(title="Management Consultant", employer="Northwind Advisory", location="Mumbai", start="2016-07", end="2019-09",
             bullets=["Advised retail and consumer clients on pricing strategy and market entry."]),
    ],
    education=[dict(qualification="MBA", field=None, institution="Fictional Institute of Management", end="2016"),
               dict(qualification="B.Tech", field="Mechanical Engineering", institution="Example Institute of Technology", end="2014")],
    skills_section=["Product strategy", "Pricing", "A/B testing", "SQL", "Team leadership", "Stakeholder management"],
    certifications=[], projects=[],
    expected_skills=["Product strategy", "Pricing", "A/B testing", "SQL", "Team leadership", "Stakeholder management",
                     "Product roadmaps"],
)

persona(
    id="P10", name="Ishaan Imaginary", date_style="mon_apos",
    jobs=[
        dict(title="Customer Success Manager", employer="Glacier SaaS Pvt Ltd", location="Chennai", start="2021-02", end=None,
             bullets=["Managed a portfolio of 45 enterprise accounts with 96% gross retention.",
                      "Ran quarterly business reviews and fed product feedback into the roadmap through Salesforce and Jira."]),
        dict(title="Account Manager", employer="Example Telecom Ltd", location="Chennai", start="2018-06", end="2021-01",
             bullets=["Grew B2B sales in the SME segment by 25% over two years."]),
    ],
    education=[dict(qualification="BBA", field=None, institution="Fictional Commerce College", end="2018")],
    skills_section=["Account management", "Customer success", "Salesforce", "Negotiation", "Communication"],
    certifications=[], projects=[],
    expected_skills=["Account management", "Customer success", "Salesforce", "Negotiation", "Communication", "Jira", "B2B sales"],
)
