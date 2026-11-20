# Demo script: 2-minute screen recording

**Setup (before recording)**
1. `make offline` (or `make site`), then open `site/index.html` full-screen in browser tab 1.
2. `make app`; open `http://localhost:8501` in tab 2. Leave it on **Graph explorer**.
3. Check `.env` has `ANTHROPIC_API_KEY`. Pre-warm the agent once: on **Ask the agent**, pick the NSWC Crane example
   and click **Ask**. This loads the prompt cache, so the recorded calls are fast.
4. Budget: each live question costs about $0.01-0.06. Total spend is $9.15; `.env` sets `BUDGET_USD=9.40` (raised from
   $9 to re-ask the questions the feed v2 changed), and the agent tab shows about how many questions are left. For
   more retakes, raise it to at most 9.90; the hard cap is $10.

Every number below is in `reports/HEADLINE.md`; if you rebuild data, re-check them there.

| Time | Screen and exact clicks | Narration |
|---|---|---|
| 0:00-0:12 | Tab 1, site, first section. Press → once to **The project in one minute**. | "This is a supplier-risk graph built from public Pentagon maintenance contracts. The customer is a made-up sustainment analyst asking: which of our contracts depend on risky suppliers, and what breaks if one fails?" |
| 0:12-0:28 | Tab 2, **Graph explorer**. Clear the *Supplier name or UEI* box, type `Alsalam Aerospace`, press Enter; keep the first candidate. | "One supplier: all three flags, $169 million, 100% sole-source, performed in Saudi Arabia. The graph shows its blast radius at a glance: every paying office, with the dollars at stake under its name, and the F-15 program above it. The line under the facts says country risk is context only: the feed beats a regional average, but my backtest couldn't test it against past terminations." |
| 0:28-0:48 | **Ask the agent** tab. In **Example questions** pick *"For office N00164 (NSWC CRANE), which flagged suppliers carry the most obligations?"*, click **Ask**, then open **Tool-call trace**. | "The assistant can only use four read-only lookups and pre-written queries. Every fact carries a record ID, and the trace shows which query produced each number." |
| 0:48-1:02 | Same tab. Replace the question with `How many awarding offices depend on Epsilon Sys Solns Mission Rotorcraft Grp LLC, directly or through subcontracts?` and click **Ask**. | "This company doesn't exist: it's a real supplier's name with one word changed. The small model used to answer for the look-alike. The name lookup now flags the missing word, so the assistant declines and names the closest real company instead." |
| 1:02-1:25 | Tab 1, press → to **Step 4: Do my risk flags predict trouble?**, then → twice (past **How the likelihood score is predicted**) to **Finding: the flags show what's at stake**. | "Then I tested my own flags against what happened next: contracts ended early the following year. Just counting a supplier's contracts predicts better, 0.758 against 0.635. Concentration's 3x is size. Sole-source suppliers are the most stable. The flags measure impact, not likelihood." |
| 1:25-1:35 | → to **What the tool shows instead: two separate scores**. | "So the tool shows the two on separate axes and highlights suppliers riskier than their size explains." |
| 1:35-1:50 | → twice to **The weak spot, and the fix**. | "On the harder test set the small model got every real question right but caught only 1/8, 3/8, 3/8 look-alike names. With the fix in the tool, not the prompt: 7/8, 8/8, 7/8." |
| 1:50-2:00 | Press Home, then → four times to **Step 2** (chart); then End for **Reproduce it**. | "Same lesson in matching: scores above 0.9 on pairs hid that only 55.8% of renamed companies resolved end to end. Comparing records at the same address took it to 82.7%. Every number is reproducible from a make target, and the whole project cost $9.15 in API calls." |

**If something goes wrong on camera**
* The agent tab shows a budget message once the guard is reached: raise `BUDGET_USD` (setup step 4) and restart
  `make app`.
* If the Epsilon question gets answered instead of declined, say so plainly. The check warns the model rather than
  blocking it, and Opus caught 7/8 such traps in each of three runs, not 8/8.
