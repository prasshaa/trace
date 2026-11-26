"""Twenty fictional, India-based resumes for the demo evaluation. None of these people are real.

Each one is marked fictional in the header. They cover three moves, with different strengths and gaps:
non-product into Associate or Product Manager, Associate into Product Manager, and Product Manager into Senior.
"""
from __future__ import annotations

from typing import Dict, List


def _p(pid, move, target_id, name, city, title, employer, bullets, education) -> dict:
    return {"id": pid, "fictional": True, "name": name, "city": city, "move": move, "target_id": target_id,
            "header": f"FICTIONAL RESUME — {name} is not a real person.",
            "current_title": title, "employer": employer, "city_label": city,
            "dates": "Jan 2022 – Present", "bullets": bullets, "education": education}


PERSONAS: List[dict] = [
    _p("F01", "non_pm_to_apm", "PM-APM", "Asha Menon", "Bengaluru", "Software Engineer", "Northwind Labs",
       ["Raised onboarding completion from 91% to 96% by rebuilding the checklist with the design team.",
        "Worked with the product manager on sprint scope. Did not own the roadmap.",
        "Supported 12 merchants during a pilot. I tracked their tickets, I did not set the success metric."],
       "B.Tech, Computer Science, Kestrel Valley Institute, 2016 – 2020"),
    _p("F02", "non_pm_to_pm", "PM-PM", "Rohan Iyer", "Pune", "QA Engineer", "Brightline Systems",
       ["Wrote regression suites that cut escaped defects by 18% over two quarters.",
        "Partnered with the product owner to clarify acceptance criteria. I did not decide what to build.",
        "Supported the launch checklist for a claims portal."],
       "B.E., Information Technology, Example Institute of Technology, 2015 – 2019"),
    _p("F03", "non_pm_to_apm", "PM-APM", "Meera Krishnan", "Chennai", "Business Analyst", "Cauvery Finance",
       ["Turned branch-staff complaints into user stories for a collections workflow.",
        "Collaborated with design on a prototype. Did not own the roadmap.",
        "Reported weekly numbers to operations. I did not define the metric."],
       "B.Com, Example College, 2014 – 2017"),
    _p("F04", "non_pm_to_pm", "PM-PM", "Kabir Shah", "Mumbai", "Consultant", "Harbour Advisory",
       ["Facilitated workshops for a retailer and wrote the recommendation deck.",
        "Worked with the client's product lead. I did not own the product after the project ended.",
        "Sized a market as 'about 2,000 stores' from the client's own slide, not from research I ran."],
       "MBA, Fictional School of Management, 2018 – 2020"),
    _p("F05", "non_pm_to_apm", "PM-APM", "Neha Kapoor", "Hyderabad", "Product Designer", "Loom & Co",
       ["Ran eight usability sessions and lifted the task-success score from 4.1 to 4.6.",
        "Partnered with engineering on the checkout redraw. Did not own the roadmap.",
        "Supported the PM by preparing the research readout."],
       "B.Des, Example School of Design, 2017 – 2021"),
    _p("F06", "non_pm_to_pm", "PM-PM", "Imran Qureshi", "Noida", "Data Analyst", "Meridian Payments",
       ["Built a SQL dashboard of payment failures for 12 merchants.",
        "Worked with the product manager to explain the charts. I did not choose the roadmap.",
        "The 12 merchants are the book I reported on, not an outcome I owned."],
       "B.Sc, Statistics, Example University, 2013 – 2016"),
    _p("F07", "non_pm_to_apm", "PM-APM", "Divya Nair", "Kochi", "Support Lead", "Palm Retail",
       ["Reduced average first-response time from 14 hours to 6 hours by rewriting the macros.",
        "Supported the product team with the top ten ticket themes each month.",
        "Did not own the product roadmap. I owned the support queue."],
       "BBA, Example College of Commerce, 2012 – 2015"),
    _p("F08", "non_pm_to_pm", "PM-PM", "Arjun Reddy", "Bengaluru", "Founder", "Tinroof Studio",
       ["Built and shipped a scheduling tool for three clinics, and owned its short roadmap.",
        "Talked to the clinic managers myself before each release.",
        "We had 40 weekly users at the peak. I did not raise outside funding."],
       "B.Tech, Example Institute, 2011 – 2015"),
    _p("F09", "apm_to_pm", "PM-PM", "Sana Iqbal", "Bengaluru", "Associate Product Manager", "Lumen Health",
       ["Ran discovery interviews with 15 clinic coordinators and wrote the problem brief.",
        "Supported the product manager on prioritisation. Did not own the roadmap.",
        "Shipped a referral form that cut duplicate data entry, with no published percentage."],
       "B.Tech, Biotechnology, Example University, 2017 – 2021"),
    _p("F10", "apm_to_pm", "PM-PM", "Vikram Joshi", "Pune", "Associate Product Manager", "Nimbus Payments",
       ["Kept the backlog ordered for one squad and wrote acceptance criteria.",
        "Partnered with engineering on a migration. I did not set the success metric.",
        "Worked with design on the empty states."],
       "B.E., Example College of Engineering, 2016 – 2020"),
    _p("F11", "apm_to_pm", "PM-PM", "Lakshmi Rao", "Hyderabad", "Associate Product Manager", "Kite Logistics",
       ["Owned the onboarding checklist for new depot managers and raised completion from 70% to 88%.",
        "Supported sales on the launch note. I did not own pricing.",
        "Did not own the multi-city rollout plan."],
       "MBA, Example Institute of Management, 2019 – 2021"),
    _p("F12", "apm_to_pm", "PM-PM", "Farhan Ali", "New Delhi", "Associate Product Manager", "Paperlane",
       ["Wrote the one-page brief for a notes feature and walked engineering through it.",
        "Worked with two customer-success managers to collect complaints.",
        "I have not presented a recommendation to senior leadership."],
       "BA, Economics, Example University, 2014 – 2017"),
    _p("F13", "apm_to_pm", "PM-PM", "Ananya Bose", "Kolkata", "Associate Product Manager", "Riverside Learning",
       ["Ran an A/B test on the signup form. The variant won by 3 percentage points.",
        "Partnered with data on the readout. Did not own the annual plan.",
        "Supported the founder in a customer call. I did not lead it."],
       "B.Sc, Example College, 2015 – 2018"),
    _p("F14", "apm_to_pm", "PM-PM", "Harsh Gupta", "Jaipur", "Associate Product Manager", "Saffron Market",
       ["Owned the roadmap for the seller-app notifications, a single surface, for two quarters.",
        "Defined the click target with the data analyst and we hit it.",
        "I have not managed another product manager."],
       "B.Tech, Example Institute, 2016 – 2020"),
    _p("F15", "pm_to_spm", "PM-SPM", "Priya Raman", "Bengaluru", "Product Manager", "Halcyon Insurance",
       ["Own the claims product for one line of business, including the quarterly roadmap.",
        "Defined the loss-ratio watch metric with finance and used it to stop one feature.",
        "I have not coached another product manager, and I do not cover a second product."],
       "PGDM, Example Institute, 2014 – 2016"),
    _p("F16", "pm_to_spm", "PM-SPM", "Nikhil Deshmukh", "Mumbai", "Product Manager", "Gateway Commerce",
       ["Grew checkout completion from 62% to 71% on the app I own.",
        "Led the decision to drop a payment method after the data showed it.",
        "Did not lead other product managers. I partnered with them on a shared design system."],
       "B.Tech, Example University, 2012 – 2016"),
    _p("F17", "pm_to_spm", "PM-SPM", "Tara Singh", "Chandigarh", "Product Manager", "Northstar Media",
       ["Presented the half-year plan to the director and got a decision on two bets.",
        "Owned the roadmap for the subscriptions area.",
        "Discovery has been thin this year: I ran three interviews, not a programme."],
       "MA, Example University, 2013 – 2015"),
    _p("F18", "pm_to_spm", "PM-SPM", "Omar Farooq", "Bengaluru", "Product Manager", "Stackyard",
       ["Own the internal platform used by four product teams and sequenced their asks.",
        "Worked with staff engineers on the API review. I did not manage them.",
        "I have not mentored a product manager. The teams I work with are engineering teams."],
       "B.E., Example College, 2011 – 2015"),
    _p("F19", "pm_to_spm", "PM-SPM", "Rhea D'Souza", "Bengaluru", "Product Manager", "Fieldnote",
       ["Owned activation for a new workspace and moved it from 22% to 31% in a quarter.",
        "Collaborated with marketing on the launch email. I did not own the campaign budget.",
        "The strategy for the second product line is the director's. I supported the write-up."],
       "BMS, Example College, 2014 – 2017"),
    _p("F20", "pm_to_spm", "PM-SPM", "Karthik Subramanian", "Coimbatore", "Product Manager", "Loom Freight",
       ["Shipped the dispatch rewrite with three squads and kept a single ordered roadmap.",
        "Defined the on-time target with operations and we moved from 81% to 90%.",
        "I have not yet taken a portfolio of products, and I have not coached a PM."],
       "B.Tech, Mechanical Engineering, Example Institute, 2010 – 2014"),
]


def _long(pid, move, target_id, name, city, roles, education) -> dict:
    return {"id": pid, "fictional": True, "name": name, "city": city, "move": move, "target_id": target_id,
            "header": f"FICTIONAL RESUME — {name} is not a real person.",
            "current_title": roles[0]["title"], "employer": roles[0]["employer"], "city_label": city,
            "dates": roles[0].get("dates", "Jan 2021 – Present"), "roles": roles, "education": education,
            "bullets": [b for role in roles for b in role["bullets"]]}


PERSONAS.extend([
    _long("F21", "non_pm_to_apm", "PM-APM", "Anika Bose", "Bengaluru", [
        {"title": "Software Engineer", "employer": "PalmPay Labs", "bullets": [
            "Built the refund API used by the support desk and cut the manual refund steps from six screens to two.",
            "Worked with the product manager on the refund policy. I did not own the roadmap.",
            "Sat in on six user calls about failed UPI payments and wrote the notes the squad used the next day.",
            "Raised the success rate of the retry job from 91% to 96% by fixing the timeout.",
            "Supported 14 merchants during a pilot. I tracked their tickets. I did not set the success metric.",
            "Partnered with design on the error states. The designer owned the layout.",
        ]},
        {"title": "Software Engineer", "employer": "CampusKart", "bullets": [
            "Shipped the seller onboarding form for a college marketplace with two other engineers.",
            "Wrote the acceptance notes for that form after sitting with two sellers.",
            "Did not choose which campus to launch in. The founder did.",
            "Kept a personal list of the top five seller complaints and shared it in the weekly meeting.",
        ]},
    ], "B.Tech, Computer Science, Example Institute, 2016 – 2020"),
    _long("F22", "non_pm_to_pm", "PM-PM", "Devika Rao", "Hyderabad", [
        {"title": "Consultant", "employer": "Harbour Advisory", "bullets": [
            "Facilitated workshops for a regional retailer and wrote the recommendation deck the sponsor signed.",
            "Worked with the client's product lead. I did not own the product after the project ended.",
            "Turned interview notes from twelve store managers into three problem statements.",
            "Sized a market from the client's own slide. I did not run the research.",
            "Brought finance and operations into one workshop and recorded the decision they left with.",
            "Coached a junior analyst on how to write a problem statement. She is not a product manager.",
        ]},
        {"title": "Business Analyst", "employer": "Cauvery Finance", "bullets": [
            "Wrote requirements for a collections workflow used by branch staff.",
            "Partnered with engineering on the first release. I did not sequence the roadmap.",
            "Reported a weekly ageing number that finance already defined. I did not define it.",
            "Shadowed five collection calls and summarised what the agents kept repeating.",
            "Supported the rollout in two branches and wrote the questions staff still had.",
        ]},
    ], "MBA, Example School of Management, 2018 – 2020"),
    _long("F23", "apm_to_pm", "PM-PM", "Ishaan Mehta", "Mumbai", [
        {"title": "Associate Product Manager", "employer": "Kite Logistics", "bullets": [
            "Own the depot-manager checklist and raised completion from 70% to 88%.",
            "Ran eleven interviews with depot managers before rewriting the checklist.",
            "Wrote the one-page requirement and the acceptance notes for that rewrite.",
            "Supported the product manager on the quarterly order of work. Did not own the roadmap.",
            "Defined a simple on-time check with operations for the checklist, not for the whole network.",
            "Partnered with design on the empty state. I did not own the visual system.",
            "Shipped the checklist to three depots and wrote what broke in the first week.",
            "I have not presented this to a director, and I have not coached another product manager.",
            "Worked with support on the top ten tickets after launch.",
            "The pricing of the depot plan is the sales lead's. I did not set it.",
        ]},
    ], "B.E., Example College of Engineering, 2016 – 2020"),
    _long("F24", "pm_to_spm", "PM-SPM", "Nandita Shah", "Pune", [
        {"title": "Product Manager", "employer": "Halcyon Insurance", "bullets": [
            "Own the claims product for one line of business, including its quarterly roadmap.",
            "Defined the loss-ratio watch with finance and used it to stop one feature.",
            "Moved straight-through processing from 54% to 67% on the claims I own.",
            "Presented the half-year recommendation to the director and got a decision on two bets.",
            "Ran a discovery round with eighteen claimants before the last rewrite.",
            "Brought operations, finance and engineering to the decision to drop a paper form.",
            "Mentored one associate product manager through her first requirement. She now writes them alone.",
            "I do not yet cover a second product, and the portfolio roadmap is the director's.",
            "Partnered with legal on the wording of the rejection notice. I did not own the policy.",
            "Wrote the post-launch note: what we shipped, what we measured, what we will not repeat.",
            "The hiring plan for the squad is the engineering manager's. I did not hire.",
            "Used the loss-ratio watch again to sequence the next quarter.",
        ]},
    ], "PGDM, Example Institute, 2014 – 2016"),
    _long("F25", "non_pm_to_apm", "PM-APM", "Farah Khan", "New Delhi", [
        {"title": "Product Designer", "employer": "Loom and Co", "bullets": [
            "Ran eight usability sessions and lifted the task-success score from 4.1 to 4.6.",
            "Partnered with engineering on the checkout redraw. Did not own the roadmap.",
            "Wrote the research readout the squad used to cut two steps from checkout.",
            "Supported the product manager by preparing the readout. She owned the decision.",
            "Sat with six shoppers in a Delhi market and noted where the paper receipt confused them.",
            "Did not set the success metric. The product manager set task success.",
            "Prototyped an empty state and tested it with four people before engineering built it.",
            "Worked with support to see the tickets that matched what shoppers said.",
            "I have not managed a roadmap and I have not coached a product manager.",
            "Shipped the redraw with the squad and wrote what still failed in the next test.",
        ]},
    ], "B.Des, Example School of Design, 2017 – 2021"),
])


def lines_for(persona: dict) -> List[dict]:
    """Stable evidence ids for the evaluation. The text is the bullet, nothing is added."""
    roles = persona.get("roles")
    if roles:
        out = []
        n = 1
        for role in roles:
            job = f"{role['title']}, {role['employer']}"
            for text in role["bullets"]:
                out.append({"id": f"{persona['id']}-L{n}", "text": text, "job": job})
                n += 1
        return out
    return [{"id": f"{persona['id']}-L{n}", "text": text,
             "job": f"{persona['current_title']}, {persona['employer']}"}
            for n, text in enumerate(persona["bullets"], 1)]


def by_id() -> Dict[str, dict]:
    return {p["id"]: p for p in PERSONAS}
