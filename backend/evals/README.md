# FrogEye evals

How do we know FrogEye is accurate? This harness measures it. It runs the app's real functions (triage, disambiguation, search, the deterministic guards, the stage-B fact-checker) on labelled datasets built from the data FrogEye actually collected, and has an independent LLM judge rate the analyses FrogEye stored. Every model answer is recorded, so `make eval` reproduces the numbers below exactly, with no API key and no cost.

## Results

From `results/latest.json` (recorded 2026-10-21; fast `gpt-5-nano`, smart `gpt-6-luna`, embeddings `text-embedding-3-small` at 512 dims; the judge is the smart model). The full tables, with every failing case, are in [results/latest.md](results/latest.md).

| Suite | Cases | Headline |
|---|---|---|
| Triage | 60 docs | precision 64.4% (29/45), recall 96.7% (29/30), F1 0.773, accuracy 71.7%; with the keyword screen in front: precision 76.3%, accuracy 83.3%. Competitor attribution as stored 69.0% (20/29) |
| Disambiguation | 28 docs | accuracy 92.9% (26/28), against 67.9% for triage alone on the same cases: the agent fixed 7 and broke none |
| Retrieval (hybrid, the default) | 25 queries | recall@8 0.967, MRR@10 1.000, nDCG@8 0.957 |
| Retrieval by mode | 25 queries | keyword 0.618 / 0.760 / 0.652, vector 0.954 / 0.980 / 0.929, hybrid + rerank 0.962 / 0.903 / 0.890 (recall@8 / MRR@10 / nDCG@8) |
| Guardrails | 58 cases | 100% of planted attacks caught (37/37), 0 false alarms (0/21); 97.3% before the number-guard fix below |
| Fact-checker | 30 claims | removes 100% of planted errors (16/16) and no true claim (0/14), after the change the eval led to; 50.0% (8/16) before (see [Experiments](#experiments)) |
| Faithfulness | 137 items of 20 analyses | judge: 56.9% supported, 40.9% partial, 2.2% unsupported; citations that resolve 100% (219/219). The judge's passages hold 97.1% of the item words found in what the writer saw |

What the numbers say:

- **The models filter, the rules keep the product clean.** The fast triage model over-files: it filed 16 of the 30 "not relevant" documents as relevant, among them four of the five items about JFrog itself, which its prompt says to reject. The app does not rely on it alone: the keyword screen stops three of those four, and recall stays at 97%, so almost nothing relevant is lost. One JFrog item (the Business Value Center post) passes both: a concrete gap to close.
- **Attribution needs the second stage.** In 5 of 60 outputs triage listed more than 5 competitors, up to the whole slug enum with repeats, and it often names no vendor for a vendor's own news (GitHub, Docker, Google Cloud). The own-feed rule raises stored attribution from 52% to 69%, and the disambiguation agent then gets ambiguous names right 93% of the time.
- **Rerank off is the right default.** The fast model's listwise rerank lowers MRR from 1.00 to 0.90 on this corpus. Keyword search alone misses almost 40% of the relevant items, which is why the keyword fallback says so in the UI.
- **The eval found the weak layer, and moved it.** The stage-B fact-checker removed only 8 of 16 planted errors: every wrong number, but 1 of 5 overstated scopes, and neither the derived total (202 CWEs from 170 + 32) nor the claim cited to the wrong source; twice its own reason called a claim unsupported while its verdict passed it. Putting the reason before the verdict and naming those error types in the prompt took it to 12 of 16; the same on the smart model to 16 of 16, still removing no true claim, for about $0.0005 more per analysis. The app now runs that (see [Experiments](#experiments)).
- **Stored analyses state their facts correctly; the framing is where they stretch.** Only 3 of 137 items are unsupported. Most "partial" ratings are about interpretation the writer adds on top of the facts ("may compete for governance attention", a JFrog capability the cited KB passage does not spell out): JFrog impact items are 43% fully supported and summaries 30%, against 70% for what happened and 100% for background.
- **The number guard had a hole.** "3" counted as present in a source that only says "3,000": a planted "On October 3" passed. The guard now matches whole numbers only (a number followed by `.` or `,` and a digit is a different number).

## The suites

| Suite | Dataset | App code it runs | Measures |
|---|---|---|---|
| `triage` | `triage.jsonl` (60) | `triage_one`, `apply_triage`, the keyword screen (`AliasMatcher`) | relevance precision/recall/F1/accuracy of the model alone and with the keyword screen first; competitor attribution as stored and in the model's own output; per case kind |
| `disambiguation` | `disambiguation.jsonl` (28) | `triage_one`, then `disambiguate_one` with its real tools (`lookup_competitor`, `search_intel` over the eval corpus) | whether the document is about the ambiguous competitor; accuracy against the triage-only baseline, cases fixed and broken |
| `retrieval` | `retrieval.jsonl` (25) | `search` (keyword fallback; hybrid; hybrid + rerank), `hybrid_search(keyword=False)` (vector only) | recall@8, MRR@10, nDCG@8 per mode and per query style |
| `guardrails` | `guardrails.jsonl` (58) | `match_quote`, `unknown_numbers_in`, `make_analysis_model`, stage B's ref cleaner | catch rate on planted attacks and false-alarm rate on genuine inputs, per attack type (no model call) |
| `fact_checker` | `fact_checker.jsonl` (5 groups, 30 claims) | stage B's `_fact_check` (its prompt, schema, model tier and re-ask) and `unsupported` (fail closed) | unsupported recall, false removal, per planted error type |
| `faithfulness` | `faithfulness.jsonl` (20 analyses) | stage B's `_factual_items`; a judge prompt of its own (`prompts/faithfulness_judge.md`) | share of items supported / partial / unsupported, citation validity, evidence coverage, per section |

Each suite reports per-case failures (case id, expected, got, and the model's or judge's reason), in `latest.md` and in the API.

Choices that make the measurement honest:

- Triage runs on every document, also those the keyword screen would have stopped, so the model is measured at its hardest; the pipeline figure is reported beside it.
- Disambiguation runs on every case, also those production would not route to the agent, so the agent is measured itself; triage alone on the same cases is the baseline.
- Retrieval indexes all 86 corpus documents whatever their relevance (as the agents' search tool sees the index) plus the 15 JFrog KB pages, uses query understanding off (its prompt holds today's date, so it cannot be replayed) and the display-only vector floor off.
- The fact-checker sees the input stage B builds (`doc:<id>` sources with their headers). The judge's prompt is not the app's checker prompt; it rates each item against passages of the text the stage-B writer was shown, picked per item, and its schema also puts the reason before the rating.

## Datasets and labels

Exported read-only from the running stack on 2026-10-21 (`psql` with `default_transaction_read_only`). Each document keeps its title, URL, publication date, source name and at most 1,200 characters of text (the faithfulness passages: about 2,000 characters per item), never a full article.

- **Corpus** (`datasets/corpus/`): 86 documents and 15 JFrog KB page excerpts, loaded with their original ids so prompts name the same documents as the recordings. Each document's `competitors` (the vendors it is about) become its competitor links.
- **triage, disambiguation, retrieval**: labelled **blind** from each document's raw fields: the export held only title, URL, date, source and excerpt, never the app's triage verdicts, signal links or relevance flags, so no label comes from FrogEye's outputs. Triage is balanced (30 relevant, 30 not) and half hard cases: word-sense traps (an AI agent "harness", network sockets, `docker run`, an AWS "wizard"), same-name products (Chainguard Harness, Microsoft's Agent Harness), JFrog's own news, headline-only items. Retrieval mixes keyword-style, paraphrased and competitor-filtered queries. [datasets/LABELS.md](datasets/LABELS.md) lists every label with its one-line reason; to change a label, edit its row and re-run.
- **guardrails**: genuine cases are real verified quotes from the evidence table and facts using the numbers their excerpt states; attacks are planted by construction.
- **fact_checker**: claims written against real excerpts, 16 of 30 with one planted error.
- **faithfulness**: no labels; a separate judge model (see below) rates the 20 most important signals analysed with the prompts of the time (`3+check3`), frozen. For each factual item and each source it cites, the export rebuilds what the stage-B writer was shown (a member document's first 1,500 words, the two chunks of a KB page closest to the signal's centroid, a prior signal's record) and keeps the sentences that best cover the item, about 2,000 characters per item in all. Those passages hold 97.1% of the item words that appear anywhere in what the writer saw, while the repo keeps about a third (36%) of each source's text.

Sample sizes set the precision of each rate: at 95% confidence a rate over 60 cases has a confidence interval of at most about ±13 points, over 28 cases ±19. The excerpts are shorter than what triage reads in production (400 words), and the keyword screen runs on the excerpt. Faithfulness is rated by a judge model separate from the writer, with a prompt and schema of its own; it reads the passages of each source the writer was shown, picked per item.

## Record and replay

Every model call goes through the app's `AccountedLLM` (ledger, cache, budget) over `recorder.RecordingBackend`. A request is keyed by a sha256 of everything that determines its answer: task, model, reasoning effort, output JSON schema, system and user text for a structured call; messages and tool definitions for a tool turn; model, dimensions and text for each embedded text (keyed per text, so batching does not matter).

- **replay** (default): answers only from `recordings/<suite>.jsonl` and never calls a model. A request that is not there is logged on the case that asked for it: the case is reported "not recorded" (with what was missing), left out of the metrics, and counted in the summary. The other cases still score; deterministic suites always do. `make eval` then exits 1, because it no longer reproduces `results/latest.json`.
- **record**: answers from the recordings when it can and makes only the missing calls live, appending them. A full record run with nothing stale drops recordings no case used.
- Record and replay hand the app the same decoded answer (embeddings round-trip through float16), the corpus is indexed one item at a time in id order, and the search tool's recency clock is pinned to the export day, so a replay sees exactly what the recording run saw. A replay uses the models in `recordings/manifest.json`, whatever the local `.env` says.
- `recordings/experiments.jsonl` keeps its own copy of every answer `evals.experiments` compares, and a full record run never prunes it; since the app adopted variant c, the fact_checker suite replays from there too.
- Size: about 450 kB in all (embeddings as base64 float16, about 1.4 kB each).

**Budget.** `--max-usd` (default $0.30) is the hard cap of the run's own ledger (the eval's `AccountedLLM` gets it as both its daily and total budget, in a fresh schema), and the recorder stops starting live calls $0.01 below it; a case stopped by the budget is reported "not recorded". `recordings/manifest.json` logs each record run's ledger spend: recording everything cost $0.0397: a two-case dry run ($0.0031), the full pass ($0.0245), the fact-checker experiments ($0.0027) and the re-recorded fact-checker and faithfulness suites ($0.0094). `cost.recorded_usd` in the results is what the replayed calls cost when recorded.

## Experiments

The first record run showed the stage-B fact-checker as the weak layer: it removed 8 of 16 planted errors, and twice its reason said "unsupported" while its verdict passed the claim, since the schema had the model write `supported` before `reason`. `python -m evals.experiments` runs the fact_checker suite's 30 claims through three variants, each through stage B's own items, sources, re-ask and fail-closed rule (recorded and replayed like the suites; `results/experiments.md`):

| Variant | Unsupported claims removed | Supported claims removed | Est. check cost per analysis | Est. stage-B cost per analysis |
|---|---|---|---|---|
| a: verdict first, prompt v3, fast model (before) | 50.0% (8/16) | 0.0% (0/14) | $0.00064 | $0.0023 |
| b: reason first, prompt v4, fast model | 75.0% (12/16) | 0.0% (0/14) | $0.00075 | $0.0024 |
| c: reason first, prompt v4, smart model (adopted) | 100.0% (16/16) | 0.0% (0/14) | $0.00113 | $0.0028 |

- **b** puts `reason` before `kind` and `supported` in the verdict schema (`make_fact_check_model(n, reason_first=True)`), so the verdict follows the reasoning, and prompt v4 names the error types it missed: a widened scope, a derived or rounded figure, the wrong actor, a fact found only in a source the item does not cite. Its examples deliberately differ from the dataset's claims.
- **c** is b on `MODEL_SMART`. It is clearly better (it also removes the four errors b still passed) and costs about $0.0005 more per analysis (+22% on stage B, about $0.03 on a day of 60 analyses).
- The costs per analysis scale production's measured stage-B costs (127 analyses: the check $0.0815, the writer $0.2087, from the main ledger) by each variant's measured check cost against a.
- Adopted: c. Stage B's fact-check now runs on the smart tier with the reason-first schema and prompt v4 (`signal_analysis.CHECK_TIER`; stored analyses record `+check4`). The newsroom's and the briefs' checkers are unchanged; they were not measured here.
- Prompt v4 was developed against this set; a held-out set is listed under next steps in the [main README](../../README.md#built-now-vs-next).

## Running

```bash
make eval          # replay: no key, no cost; exits 1 unless it reproduces results/latest.json
make eval-record   # record what is missing (needs OPENAI_API_KEY in .env), capped at $0.30
```

Without `make`, the same commands are `docker compose -f docker-compose.test.yml run --rm -T eval python -m evals.run [args]`. Options: `--suite NAME` (repeatable), `--mode replay|record`, `--max-usd X`, `--limit N` (the first N cases of each suite: a cheap dry run; nothing is written to `latest.*`).

The harness runs only in the compose project `frogeye-test`. It uses `TEST_DATABASE_URL` and refuses a database whose name does not contain "test", because it recreates the `public` schema there (migrations, seed, corpus), as the pytest suite does: do not run it while pytest runs. A replay writes `results/replay.{json,md}` (git-ignored); a record run writes `results/latest.{json,md}`, which the API serves at `GET /api/v1/evals`. CI replays on every push.

## Adding a case

1. Append a row to the dataset's `.jsonl` with a new `id` and a `label_source`. A document not yet in the corpus goes into `datasets/corpus/documents.jsonl` first (its original id, at most 1,200 characters of text, its `competitors`).
2. Run `make eval`: the new case is reported "not recorded".
3. Run `make eval-record`: it makes only the calls the new case needs (typically well under a cent) and rewrites `results/latest.*`.
4. Commit the dataset, the recordings, the manifest and the results together.

A deterministic case (guardrails) needs no recording: `make eval-record` rewrites the results at no cost. Changing an app prompt makes every case that uses it stale; `make eval-record` re-records them.
