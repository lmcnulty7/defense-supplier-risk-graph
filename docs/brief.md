# Brief: where sustainment work depends on fragile suppliers

*One page for a non-technical reader. Portfolio project on public data; the customer is fictional; not used by,
endorsed by, or built for the DoD or any company.*

**Bottom line up front.** Public contract data can show, in seconds and with a source for every number, which
maintenance contracts depend on suppliers that dominate an office's spending, win work without competition, or
work in risky places, and what else would be hit if one of them failed. An assistant answered 87 of 88 test
questions correctly, declined all 13 questions the data cannot answer, and never cited a record that doesn't exist.

**What we looked at.** Every Defense Department contract for maintenance, repair and overhaul of equipment with
activity from October 2022 through September 2025: 57,596 contracts worth $225 billion over their lifetimes,
35,842 reported subcontracts, and 11,658 suppliers.

**What we found**

* **Sole-source is the norm in sustainment, not the exception.** About half of all dollars (49%) went to
  contracts that were not competed. Companies that build a platform usually maintain it.
* **So the warning flags are a filter, not a verdict.** Nearly 87% of dollars (86.75%) go to a supplier with at
  least one flag (concentrated, sole-source, or overseas exposure). The useful question is "if this one fails, how
  much breaks?" The tool answers that for any supplier.
* **The flags say how much depends on a supplier, not whether it is about to fail.** We checked them against what
  actually happened the following year (contract terminations and cancellations in public records). As a predictor
  the flags scored 0.635 on a 0.5 (coin flip) to 1 (perfect) scale, worse than simply counting a supplier's contracts
  (0.758). Flagged suppliers were not more likely to have a contract terminated once their size is accounted for, and
  sole-source suppliers were *less* likely. So the tool now shows two separate things: what is at stake, and a separately tested estimate
  of how likely a disruption is.
* **Example.** One Saudi-based aerospace company has all three flags. It received $169 million on six
  non-competed contracts, all performed in Saudi Arabia, spread across four contracting offices and one aircraft
  program (F-15).
* **Example.** At one large Navy contracting office (NSWC Crane), 215 of its 290 suppliers carry a flag, covering
  77% of the office's dollars.
* **Hidden dependencies are under-reported.** Under 4% of contracts (3.8%) list any subcontractors, so the
  second-tier picture is a floor, not a ceiling.
* **The country-risk input is context, not a forecast.** The regional escalation feed now beats a simple regional
  average, but it only has forecasts from late 2026, so there is no way to check whether it would have predicted the
  2023-2025 contract terminations. Until that can be tested, the tool shows *where* exposure sits and labels the
  escalation numbers as background; they don't drive the likelihood score.

**How much to trust it**

* Supplier name matching: when a renamed company's record arrives without its ID, fixing how candidates are found
  raised correct matches from 56% to 83%. A stricter setting, chosen on half the test records and checked on the other
  half, then traded a few correct matches (to about 78%) for far fewer mistakes: under 1% matched to the wrong company,
  and companies missing from the data wrongly matched about 4% of the time instead of 9%. It handles acronyms ("GDLS"
  for General Dynamics Land Systems).
* Assistant: 87 of 88 standard questions and all 38 harder questions answered correctly, with every citation
  traceable to a real record. Its weak spot was a company name that is one word off from a real one. A check in the
  name lookup now flags those: the smaller, 49-times-cheaper assistant went from catching 1, 3 and 3 of 8 such names
  in three runs to 7/8, 8/8, 7/8, about as well as the large one.
* Cost: about 3 to 4 cents and 7 to 10 seconds per question. The whole build used $9.01 of a $10 AI budget.

**What would make it decision-grade:** supplier performance and financial data (to predict failure, not just
terminations), a country-risk feed that has demonstrated forecasting skill, complete subcontract reporting, and the
analyst's own program list to scope the questions.
