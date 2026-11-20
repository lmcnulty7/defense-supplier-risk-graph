# Problem statement

> **Fictional engagement.** The customer below is a persona invented for this portfolio project. All data is public
> (USAspending, SAM.gov, and a MENA risk feed from a separate personal project). Nothing here is used by, endorsed
> by, or built for the DoD or any company.

## The customer and the question

A sustainment analyst in a (fictional) defense program office keeps fielded equipment running by buying repair,
overhaul and maintenance services. Their recurring question:

> "Which of our contracts and programs depend on suppliers that are **concentrated**, **sole-sourced**, or
> **geographically exposed**, and what is the **blast radius** if one of them fails?"

Today that answer means hours of USAspending searches, spreadsheet joins across prime awards and
subaward reports, manual name matching (one company appears under many UEIs and spellings), and a separate look
at country risk. The work is slow and not reproducible, and nobody can trace a number back to its source record.

## What "done" looks like for the analyst

1. A **supplier graph** linking awards, suppliers (resolved across spellings and UEIs), parent companies, awarding
   offices, product/service codes and places of performance, with provenance on every record.
2. **Risk screens** per supplier: office concentration, sole-source share, dependent offices, centrality, and
   exposure to countries with an escalation-risk estimate.
3. An **assistant** that answers dependency questions in plain English, cites the award and entity IDs behind every
   claim, and says when the data cannot answer.
4. **Evidence that it works**: entity-resolution precision/recall and an evaluation harness that scores the assistant
   (correctness, citations, refusals, cost, latency) and runs as a regression suite.

## Scope decisions

* **Data slice:** DoD prime contracts (types A/B/C/D) and procurement subawards under product/service code
  category **J (maintenance, repair and rebuilding of equipment)**, action dates **FY2023-FY2025**. That is 57,596 prime
  awards and 35,842 subawards. It fits on a laptop, so no narrowing to a single service was needed.
* **Out of scope:** spare-parts optimization; free-form query generation by the assistant (read-only, named
  queries only in this version); classified or non-public data; any operational use.

## Success criteria

See `docs/requirements.md` for the testable requirements and acceptance criteria. Headline targets:
entity-resolution precision and recall reported separately on identifier-derived and synthetic labels; assistant
answer correctness >= 90% and correct-refusal rate >= 90% on the golden set; zero fabricated citations; total LLM
spend under $20 (raised from $10 by the owner on 2026-11-19).
