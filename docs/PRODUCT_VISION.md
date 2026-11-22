# Trace product vision

## What Trace does

Trace helps working professionals understand how to move from their current career position to a role they want.

The central question:

> **"Given my experience and the role I want to reach, what are my most relevant options for getting there, and what
> should I actually do next?"**

For a Software Engineer who wants to become a Product Manager, Trace should explain:
- what their existing experience already offers;
- what they need to learn or develop;
- how to gain relevant practical experience and how to demonstrate it;
- which transitions are realistic;
- whether to target the role directly or through an intermediary role;
- how to position themselves for hiring;
- what people with relevant backgrounds have actually done;
- which resources and programmes may help.

## What Trace is not

Trace is not:
- a generic career chatbot;
- a resume builder;
- a job board;
- a collection of generic tips;
- a chart of job-title transitions;
- a ranking of the most common moves;
- an LLM inventing career advice from a prompt.

## Why it is different

Trace combines four things:
- the **individual's structured, confirmed career profile**;
- **eligible real-world career histories**, consented and de-identified;
- **verified target-role knowledge**;
- a **transparent, deterministic recommendation engine**.

## The core principle

| Source | Role in a recommendation |
|---|---|
| The user's confirmed profile | Decides **personal relevance** |
| Eligible career histories | Provide **observed precedent** and supporting evidence |
| External target-role knowledge | Provides **requirements, guidance and resources** that the histories may not contain |

These three sources stay distinguishable in data, in API responses and in the interface.

**Prevalence is not probability.**
- The number of people who followed a route never decides by itself whether it is the best route for this user.
- Frequency is not suitability.
- Observed transitions don't prove effectiveness or causation.

## The output: a Career Intelligence Report

The report contains a primary pathway and, where they genuinely exist, alternatives. Each pathway runs through four
connected stages:

1. **Build your foundation:** the knowledge the target role needs.
2. **Gain practical experience:** real work, internal opportunities, shadowing, projects, discovery.
3. **Become application-ready:** positioning, credible proof, case studies, interview preparation.
4. **Get into the target role:** direct and intermediary entry routes, relevant openings and companies, referrals,
   interviews, verified programmes.

Stage 4 is a core differentiator. It appears in the opening summary, not at the bottom of the report.

## The product test

Two people with the same target and different backgrounds should receive materially different reports. Take a Software
Engineer from B2B SaaS and a Business Analyst from fintech, both aiming for PM. They should get:
- different strengths and gaps;
- different preparation;
- different relevant histories;
- different entry options.

Both should still get a complete four-stage pathway. The evidence supports the recommendation; it does not dictate it.

See `PRODUCT_PRINCIPLES.md`, `USER_JOURNEY.md` and `RECOMMENDATION_ENGINE.md`. For what is built today, see
`IMPLEMENTATION_PLAN.md`.
