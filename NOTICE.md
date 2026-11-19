# Notice: data attribution

The MIT license in `LICENSE` covers this repository's code and documentation. It does not relicense the third-party
data below, which keeps its own terms. Details and how this repository complies: [docs/DATA_LICENSES.md](docs/DATA_LICENSES.md).

| Data in this repository | Source and terms |
|---|---|
| `data/cache/usaspending*/` (DoD PSC-J prime awards, subawards, contract transactions) | [USAspending.gov](https://www.usaspending.gov), U.S. Department of the Treasury. Federal spending data published for public use. Retrieved through the public bulk-download API; request bodies, pull dates and SHA-256 hashes are stored next to each file. |
| `data/cache/sam/entities.jsonl` (67 public entity records) | [SAM.gov](https://sam.gov) Entity Management API v3, U.S. General Services Administration, public version only ([terms of use](https://sam.gov/about/terms-of-use)). |
| `data/feeds/mena/` (country escalation probabilities, model outputs only) | Produced by the author's separate MENA escalation model, trained on the [UCDP](https://ucdp.uu.se) Georeferenced Event Dataset and Candidate Events Dataset (CC BY 4.0) and [The GDELT Project](https://www.gdeltproject.org/). UCDP citations, as given by the producer: Sundberg & Melander (2013); Davies, Pettersson & Öberg (2026); Hegre et al. (2020); ucdp.uu.se. |
| `fixtures/risk_feed_fixture.csv` | Synthetic values for tests. |
| `logs/agent_runs/`, `logs/spend_ledger.jsonl` | Claude API transcripts and costs from this project's own runs on the public data above. |

No data here comes from ACLED. Not used by, endorsed by, or built for the U.S. Department of Defense, any
government agency, or any company.
