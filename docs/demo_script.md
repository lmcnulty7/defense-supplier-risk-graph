# Demo script: 2-minute screen recording

**Setup (before recording)**
1. `make app`; open `http://localhost:8501` full-screen. It opens on **Overview**.
2. Check `.env` has `ANTHROPIC_API_KEY`. Pre-warm the agent once: on **Ask the agent**, pick the second example
   (*"If Sikorsky Aircraft Corporation failed, which offices would have no other supplier already doing the same
   work?"*) and click **Ask**. This loads the prompt cache, so the recorded call is fast. Then click **Overview**.
3. Budget: each live question costs about $0.01-0.06. Total spend is $16.74; `.env` sets `BUDGET_USD=19.40`, and the
   agent tab shows about how many questions are left. The hard cap is $20.
4. Optional, for questions afterwards: `make site` and open `site/index.html` in a second tab (the full write-up).

Every number below is in `reports/HEADLINE.md` or comes from a tested query; if you rebuild data, re-check them in
the app.

| Time | Screen and exact clicks | Narration |
|---|---|---|
| 0:00-0:15 | **Overview** tab. Point at the three facts. | "This is a supplier-risk graph built from public Pentagon maintenance contracts. The customer is a made-up sustainment analyst asking which contracts depend on risky suppliers, and what breaks if one fails. 86.75% of maintenance dollars go to a supplier with a risk flag, 188 of 757 offices rely on one sole-source supplier for over half their spending, and the flags turned out to measure impact, not likelihood." |
| 0:15-0:35 | On the **Sikorsky Aircraft** card, click **Graph explorer**. Hover the dashed line to ACC-RSA (W9124P). | "Here's one supplier's blast radius. Each green node is an office that would lose work, labeled with Sikorsky's own dollars there; thick lines are more money. Dashed lines are offices it only reaches as a subcontractor: here the prime contracts are $14 million, but Sikorsky's own share is $7.7 million, and the hover says so." |
| 0:35-0:55 | Click **What if it fails?** (above the graph). | "What if it fails? Seven offices and $167 million. The useful part is the last column: for each office, who else already does the same type of work for it. At DCMA Stratford, nobody does. That's an alternative in the same service code, not proof someone could take over, and the screen says so." |
| 0:55-1:15 | In the table, click the **DCMA Stratford (S0707A)** row. | "Clicking an office opens the office view. One supplier gets 100% of this office's spending: a concentration index of 10,000 out of 10,000. Every supplier and office is clickable, and Back returns to where I was." |
| 1:15-1:45 | Click **Ask the agent**. In **Example questions** pick the second question (*"If Sikorsky Aircraft Corporation failed, which offices would have no other supplier already doing the same work?"*), click **Ask**. | "The assistant runs the same scenario as a read-only tool and cites a record for every fact. It can only use five lookups and pre-written queries, so it can't invent a query, and if the data can't answer, it says so." |
| 1:45-2:00 | Click one of the **Ask next** buttons; it fills the question box. Don't ask it. | "Follow-ups come from rules, not another model call. On the harder test set, a check in the name lookup took the small model from 4/8, 4/8, 4/8 look-alike names caught to 8/8, 8/8, 8/8. Every number is reproducible from a make target, and the whole project cost $16.74 in API calls." |

**If something goes wrong on camera**
* The agent tab shows a budget message once the guard is reached: raise `BUDGET_USD` (setup step 3, at most 19.90) and
  restart `make app`.
* If the agent's answer differs from the scenario tab, say so plainly: the tab is the deterministic query, the agent is
  a model reading the same query's output.
* If a graph is still settling when you hover, wait a second: it re-fits itself once the layout is done.
