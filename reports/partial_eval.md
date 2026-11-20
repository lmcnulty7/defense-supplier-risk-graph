# Partial-answer regression

Scored by `make partial-offline` from `logs/agent_runs/partial_*.jsonl` (last attempt per question). Each question has one part outside the data (a country the MENA risk feed does not cover) and one answerable part (who works there). See `src/supplier_graph/evalh/partial.py` for the checks.

**2/2 passed.**

| question | passed | failed checks | no_data reason |
|---|---|---|---|
| What is the escalation risk for Japan? | yes | none | Japan is not covered by the MENA escalation-risk feed |
| How risky is Bahrain right now, and who depends on work there? | yes | none | The MENA risk feed has no escalation estimate for Bahrain |
