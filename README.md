# The Bioprocess Brief

A Python project that discovers new papers, **ranks them for your own project at no cost**, writes a standalone `index.html`, publishes it with GitHub Actions and GitHub Pages every **Monday at 07:00 UTC**, and can email you the top picks. The deployed default uses **no AI and no paid API**: papers are ranked by transparent rules (weighted keywords, watched authors, and your seed papers), and each card says exactly why it was picked.

The configured focus is NRF1-driven mitochondrial biogenesis, AXER/SLC35B1 ER ATP supply, and CHO secretory productivity, within broader bioprocessing and cell-engineering interests. Optional AI summaries can use **Gemini's free tier**, or paid **OpenAI** or **Anthropic** keys. Python 3.11 or newer is required; there are **no external Python dependencies**.

## Personalise your brief (free)

Edit the `personal` section and `research_profile` in `config.json`:

| Setting | What it does |
| --- | --- |
| `seed_papers` | DOIs of papers central to your work. New papers that **cite** them are pulled in from OpenAlex and boosted, and papers with **similar text** score higher. This is the strongest personal signal; add 10–20 (your own papers, key papers in the field). |
| `watch_authors` | People you follow, as `"Given Family"` (25 CHO groups are preset). A full first-name match, or at least two matching initials such as `Lee, J. S.`, earns `watch_author_points`. A single shared initial such as `Young, J.` earns half and is labelled "initial-only match", because names like Lee, Yang, and Young are common. Use the name form people publish under, e.g. `C. Mark Smales`. |
| `block_weights` | How much each `search.blocks` topic is worth. A term in the title counts 1.5×. |
| `weak_terms` | Broad or ambiguous terms (for example `ER stress`, `OXPHOS`, `PRC`) that count only 0.3× on their own. |
| `exclude_terms` | Terms that subtract `exclude_penalty` points, such as a model system you never use. |
| `top_n`, `min_score` | How many papers are featured and emailed, and the minimum match points. |
| `ai_shortlist` | In AI mode, only this many top-ranked papers are sent to the model, which keeps Gemini within free limits. |
| `research_profile` | Plain-language description of your project. AI mode scores against it and writes "for your project…" explanations. |

Every point a paper earns is listed on its card under **Why it is in your brief**. If good papers are missing, add seed papers or terms; if noise gets through, move terms to `weak_terms` or `exclude_terms`.

OpenAlex works without a key, but anonymous requests share a small daily budget per IP address, which GitHub's shared runners can exhaust. A free key from [openalex.org/settings/api](https://openalex.org/settings/api), saved as the repository secret `OPENALEX_API_KEY`, makes citation alerts reliable.

## Set up GitHub in a few steps

1. Create a GitHub repository and upload **the contents of this project folder** into its root. Include the hidden `.github` directory. The workflow must end up at `.github/workflows/workflow.yml`, alongside the root `config.json` and `literature_brief/` directory.
2. Leave the repository Actions variable `BRIEF_MODE` unset, or set it to `fetch-only`. This is the workflow default. It retrieves papers, keyword matches, and available abstracts without contacting an LLM provider. **No OpenAI API credits or API key are needed.** A saved API key remains unused in this mode.
3. Optionally add the Actions variable `CONTACT_EMAIL` for polite metadata API requests. The variable is not used for email delivery.
4. Go to **Settings → Pages → Build and deployment → Source** and select **GitHub Actions**. Choose a public repository for the simplest Pages setup; private-repository Pages availability depends on your GitHub plan.
5. Commit the workflow on your repository's **default branch**, then open **Actions → Weekly literature brief → Run workflow**. Select the default branch and run it once. After a successful deployment, the `github-pages` environment links to your brief.

The workflow then runs weekly with `cron: "0 7 * * 1"`. It uses GitHub's deployment artifact flow, so you do not need a `gh-pages` branch or a personal access token. Pull requests run offline tests without API calls or deployment. Manual runs on other branches do not deploy.

You can enable AI summaries later **without paying**, using Gemini's free tier:

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey); the free tier needs no billing account.
2. Under **Settings → Secrets and variables → Actions → Secrets**, add `GEMINI_API_KEY`.
3. Under **Variables**, add `BRIEF_MODE` with value `ai`. `config.json` already selects `llm.provider: gemini`.

Only the `personal.ai_shortlist` top papers (30 by default) are sent to the model each week, so a run stays small. Free-tier limits vary by model and can change; check yours in AI Studio. Under Google's terms, content sent to the free tier may be used to improve Google's products. Paper titles and abstracts are public, but do not add private notes to `research_profile` if that matters to you. Paid providers remain available: set `LLM_PROVIDER` to `openai` or `anthropic` and add `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. `LLM_MODEL` can optionally select a compatible model; the defaults are `gemini-3.5-flash-lite`, `gpt-4.1-mini`, and `claude-haiku-4-5`. To return to the free ranking only, remove `BRIEF_MODE` or set it to `fetch-only`. The only accepted mode values are `fetch-only` and `ai`; other values stop generation with an error.

GitHub schedules can start late under load, especially at the top of the hour, and public repositories' schedules can be disabled after 60 days without repository activity. Check Actions if an expected edition is missing. See [GitHub scheduling behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) and [Pages workflow setup](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Preview and run locally

Open `preview/index.html` for the included **fictional demonstration**. Its sample papers, authors, findings, and scores are synthetic; no paper or LLM API was called to create it.

From this project directory, using Python 3.11+:

```bash
# Rebuild a demo without credentials or network access.
python3 -m literature_brief --demo --output preview

# Default deployed mode: real papers, keyword matches, and available abstracts; no LLM calls.
python3 -m literature_brief --fetch-only --output site

# Optional paid AI mode: build a scored brief with a funded OpenAI API account.
export OPENAI_API_KEY='YOUR_API_KEY'
python3 -m literature_brief --output site

# Alternatively, use Anthropic; changing provider selects its default model.
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY='YOUR_API_KEY'
python3 -m literature_brief --output site

# Optional: override the model for your selected provider.
export LLM_MODEL='YOUR_COMPATIBLE_MODEL_ID'

# Reproduce a discovery-only window ending at the start of a given UTC date.
python3 -m literature_brief --fetch-only --end-date 2026-10-19 --output site

# Run the offline test suite.
python3 -m unittest discover -s tests -v
```

API keys are read from environment variables only. `.env.example` documents the settings, but `.env` files are **not loaded automatically**. Never put keys in `config.json`, generated files, or Git commits.

The `BRIEF_MODE` switch belongs to the GitHub workflow. When running locally, pass `--fetch-only` for no AI calls; a plain run without that flag requests AI assessment.

The normal output is `site/index.html` plus `site/brief.json` and `.nojekyll`. `brief.json` contains all retained metadata, assessment statuses, focused keyword matches, and search-block definitions, including papers below the relevance threshold, without abstracts, or excluded from scoring by an optional cap. The Pages workflow uploads only `site/`.

## Sources and coverage

Discovery uses documented metadata APIs rather than scraping publisher websites:

| Source | Retrieval |
| --- | --- |
| bioRxiv | Direct API, full date-range pagination; first versions only; limited to `sources.biorxiv_categories` |
| Papers citing your seed papers | OpenAlex `cites:` filter, any journal |
| Nature Biotechnology, Nature Communications, Nature Metabolism, Nature Chemical Biology, Nature Cell Biology, Communications Biology, Scientific Reports | Crossref ISSNs in `config.json` |
| PLOS One, PLOS Computational Biology, PLOS Biology | Crossref ISSNs in `config.json` |
| mAbs, Journal of Biotechnology, ACS Synthetic Biology, Cell Reports, Cell Metabolism, Molecular Cell, Journal of Biological Chemistry, eLife | Crossref ISSNs in `config.json` |
| Biotechnology and Bioengineering | Crossref ISSN `1097-0290` |
| Metabolic Engineering | Crossref ISSN `1096-7176` |
| Trends in Biotechnology | Crossref ISSN `0167-7799` |
| Nucleic Acids Research | Crossref ISSN `1362-4962` |
| PNAS | Crossref ISSN `1091-6490` |
| Biotechnology Journal | Crossref ISSN `1860-7314` |
| Biotechnology Progress | Crossref ISSN `1520-6033` |
| Biotechnology Advances | Crossref ISSN `0734-9750` |
| Biochemical Engineering Journal | Crossref ISSN `1369-703X` |
| Molecular Therapy | Crossref ISSN `1525-0016` |
| Molecular Therapy Advances | Crossref ISSN `3117-387X` |
| Molecular Therapy Methods & Clinical Development | Crossref ISSN `2329-0501`, retained for historical windows |

The NLM catalog identifies *Molecular Therapy Advances* as the continuation of *Methods & Clinical Development*, which ended in 2025. Both are configured to support current and historical windows. [NLM title record](https://www.ncbi.nlm.nih.gov/nlmcatalog/101624857).

Crossref supplies journal articles' titles, authors, DOI URLs, dates, and available abstracts. Missing abstracts are looked up by DOI in **Europe PMC**, in batches. Some publishers do not supply abstracts or complete author lists, and Europe PMC may not yet have a matching record. Missing data remains explicit; the model never fills in bibliographic fields. Papers without abstracts remain available for manual review and receive no invented score or summary.

Whole-publisher DOI prefixes (such as Nature's `10.1038`) are still supported, but they pull in thousands of unrelated papers a week, so the defaults list journals individually. Metadata deposits and indexing can be delayed, so this is a discovery brief, not guaranteed exhaustive journal coverage. Strict seven-day windows can miss papers indexed after their publication week. Use `--end-date` to rerun a past window if needed.

To add a journal, append its registered ISSN to `sources.crossref` in `config.json`:

```json
{"name": "Your journal", "issn": "1234-567X"}
```

This ISSN is a format example, not a real configured journal. Use a verified print or electronic ISSN. A source entry must specify exactly one `issn` or `prefix`. Sources are fully paginated; there is no hidden discovery cap. DOI deduplication removes repeated metadata records. A preprint and its final journal article generally have different DOIs and remain separate records, with preprints clearly marked.

## Dates, assessment, and display

The window is **seven complete UTC calendar days**, ending at midnight at the start of the run date. For a Monday run, it covers the previous Monday through Sunday. For example, `--end-date 2026-10-19` covers October 12–18 inclusive. Monday morning publications appear in the following edition. Most APIs expose day-level dates, so the project does not imply exact timestamp precision.

The earliest known online or print publication day is used. Crossref deposit, creation, and indexing dates are never substituted for publication dates. Imprecise earlier dates are excluded and reported as partial coverage. bioRxiv revisions of older papers are excluded.

In the deployed free default, the top `personal.top_n` papers with at least `personal.min_score` match points are featured with their reasons. Every other discovered paper stays available, closest match first, under **Other papers this week**. No AI summary is requested. If no `personal` section is configured, all papers are shown newest first, as before.

In optional AI mode with a `personal` section, only the `ai_shortlist` highest-ranked papers with abstracts are assessed; without one, every paper with an abstract is scored unless the assessment cap is set. The model returns a validated object with:

- `relevance`: integer from 1 to 10, based on the interests in `config.json`.
- `methodology`: the first summary bullet, grounded in the abstract.
- `key_findings`: the second summary bullet, grounded in the abstract.
- `rationale`: a short explanation of topic fit and material transferability limits.

Scores 9–10 mean the paper directly tests, extends, or supplies a method for the core question in `research_profile`; 7–8 indicate clearly relevant enabling work; 5–6 indicate a plausible indirect application; 1–4 indicate limited connection. The prompt distinguishes reviews from experiments and treats microbial-to-mammalian transfer cautiously. Paper text is treated as source data, never as instructions. Scores measure topic fit, not scientific quality.

In AI mode, the HTML shows the top 20 papers scoring at least 6 by default, ordered by relevance and then publication date. Other assessed and unscored papers remain accessible in expandable groups. Both modes include search, publication-type and focused-combination filtering, expandable abstracts, full author lists when available, and JSON download. The page uses local CSS and JavaScript and remains readable with JavaScript disabled.

## Focused keyword combinations

The six configured search blocks are **host / system**, **NRF1 and mitochondrial biogenesis**, **ER ATP supply and nucleotide transport**, **energy / redox state manipulation**, **phenotype / readout**, and **biosensors**. `search.blocks` in `config.json` holds their terms, and `search.combinations` lists the focused combinations below.

Terms within a block use **OR**: any one term can satisfy that block. Selected blocks use **AND**: a paper must contain at least one term from every block in a combination. The default runs separate focused combinations:

| Combination | Required blocks |
| --- | --- |
| Host + mitochondria + ER ATP | Host / system, mitochondrial biogenesis, ER ATP |
| Host + mitochondria + energy | Host / system, mitochondrial biogenesis, energy / redox |
| Host + mitochondria + phenotype | Host / system, mitochondrial biogenesis, phenotype |
| Host + ER ATP + energy | Host / system, ER ATP, energy / redox |
| Host + ER ATP + phenotype | Host / system, ER ATP, phenotype |
| Host + energy + phenotype | Host / system, energy / redox, phenotype |
| Host + biosensors | Host / system, biosensors |

These are local matches against each retrieved paper’s **title and available abstract**, after source discovery and DOI deduplication. They are not Boolean queries sent to external literature databases, and they do not search full text or expand coverage beyond the configured sources and date window. A missing keyword match never removes a paper. In AI mode, papers with abstracts also receive the normal relevance assessment, subject to the optional assessment cap. A paper can match several combinations. Papers without abstracts can match through their title, and the missing abstract remains explicit.

The HTML’s **Focused search matches** section shows combination counts and links to matching paper cards. Cards show the matching combinations, terms from each block, and terminology notes. The combination filter is available when JavaScript is enabled; the search index, paper links, match details, and all records remain readable without JavaScript. `brief.json` includes per-paper `focused_search` metadata and the report-level `focused_searches` summary. A zero count means no match was found in the retrieved titles and available abstracts for that window; it does not establish that no relevant papers exist.

Keyword matches are signals for manual review, not confirmation of a mechanism. **NRF1 / NRF-1 is ambiguous**: nuclear respiratory factor 1 and NFE2L1 use overlapping names. **GABPA**, called NRF-2 in some mitochondrial-biogenesis literature, is distinct from **NFE2L2**, commonly called NRF2. NRF1 receives a terminology note when respiratory context is absent or NFE2L1 is also mentioned; PRC is flagged when respiratory context is absent. This heuristic cannot resolve every use, so inspect the paper to identify the gene or coactivator. See the NCBI records for [NRF1](https://www.ncbi.nlm.nih.gov/datasets/gene/4899/) and [NFE2L1](https://www.ncbi.nlm.nih.gov/gene/4779), and the [review discussing NRF-2 nomenclature](https://pmc.ncbi.nlm.nih.gov/articles/PMC11176867/). Other broad terms and abbreviations can also be nonspecific, so a combination match alone does not imply direct CHO relevance or a causal link between its blocks.

To change the focus, edit terms within `search.blocks` or select block IDs in `search.combinations`; remove the optional `search` object to disable focused matching. This does not change the broad journal/date discovery or the configured LLM scoring interests.

## Adjust volume and API use

The workflow uses the repository Actions variable `BRIEF_MODE` to select `fetch-only` (default) or `ai`. Discovery-only runs do not use LLM credentials or credits. The AI-related settings below apply only to AI mode. Edit `config.json`:

| Setting | Default | Effect |
| --- | --- | --- |
| `interests` | Your detailed research topics | Used in scoring and cache invalidation |
| `interest_labels` | Short topic labels | Display only |
| `search` | Six keyword blocks and seven focused combinations | Local title/abstract annotations and focused match index; optional |
| `min_relevance` | `6` | Minimum score for the featured/relevant groups |
| `top_n` | `20` | Featured cards; `0` shows all relevant papers up front |
| `max_papers` | `0` | Maximum papers sent for assessment; `0` scores all available abstracts |
| `llm.provider` | `gemini` | `gemini` (free tier), `openai`, or `anthropic` |
| `llm.model` | `gemini-3.5-flash-lite` | A compatible model; change it with the provider or leave empty for its default |
| `sources.biorxiv_categories` | Nine life-science categories | bioRxiv categories to keep; `[]` keeps all |
| `llm.workers` | `3` | Concurrent assessments; lower this if your account has tight rate limits |
| `llm.max_output_tokens` | `1000` | Maximum output tokens per assessment |
| `sources.request_interval_seconds` | `0.15` | Pause between sequential metadata requests |

bioRxiv and the broad Nature/PLOS prefixes can return thousands of papers in a week. Discovery-only mode makes no LLM requests regardless of volume. If AI mode is enabled, it makes one LLM request for each uncached paper with an abstract by default, so API use depends on volume and model. `top_n` controls AI-mode display only. Set `max_papers` to limit assessment requests; the newest papers are then assessed first, the remaining metadata stays in the brief, and a visible notice identifies the excluded count. It is a request cap, not a monetary budget; retries may issue additional requests.

Assessments are cached by metadata, abstract, research interests, provider, model, token limit, and prompt version. Repeat runs can reuse matching results. GitHub caches are opportunistic and may expire, so cache availability is not guaranteed. The cache contains no API keys and is not deployed to Pages. HTTP retries are bounded and use backoff for rate limits and transient failures.

## Failure behavior and implementation

Individual source or paper failures remain visible in the generated brief. A total discovery failure returns a nonzero exit code and prevents the workflow from replacing the previous Pages edition. In AI mode, failure to assess any eligible paper also prevents deployment. Discovery-only runs do not require assessments to succeed. Local diagnostic output is still written when the report can be assembled. A successful empty search is shown differently from a failed search. Partial success can publish with visible coverage or assessment notices.

The workflow allows up to 180 minutes for discovery and scoring. A timeout stops deployment; reduce scope or concurrent workload as appropriate. Keys are available only to the generation step. The build job has read access to repository/Pages metadata; only the deployment job receives Pages write and OIDC permissions.

```text
.github/workflows/workflow.yml   Weekly schedule, offline tests, cache, Pages deployment
config.json                      Topics, model, journals, display settings
literature_brief/sources.py       bioRxiv, Crossref, Europe PMC, dates, deduplication
literature_brief/scoring.py       Provider adapters, prompt, validation, cache
literature_brief/search.py        Focused block matching and terminology notes
literature_brief/personal.py      Free personal ranking, seed papers, OpenAlex citation alerts
literature_brief/mailer.py        Optional weekly email of the top picks over SMTP
literature_brief/pipeline.py      Assessment orchestration and failure decisions
literature_brief/render.py        Standalone HTML and JSON export
preview/index.html               Fictional offline demonstration
tests/                           Mocked tests; no keys or network needed
```

API contracts were checked against the official [bioRxiv API](https://api.biorxiv.org/), [Crossref filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/), [Crossref pagination guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/), [Europe PMC REST API](https://europepmc.org/RestfulWebService), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), and [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

The supplied preview and tests run without credentials. A live discovery-only brief and its GitHub deployment require the repository/Pages setup above, with no LLM key or credits. Optional AI assessment requires the selected provider's funded API key.

## Email delivery

After each successful deployment, the `email` job sends the week's top picks (AI-assessed picks in AI mode, otherwise the free ranking) with a link to the full brief. It uses your own mail account over SMTP, so it is free. Until it is configured, the job is skipped successfully.

Use an **app-specific password**, never your normal account password. For iCloud Mail, create one at [account.apple.com](https://account.apple.com) → Sign-In and Security → App-Specific Passwords. For Gmail, enable 2-Step Verification and create an App Password. Then add, under **Settings → Secrets and variables → Actions**:

| Name | Kind | iCloud example | Gmail example |
| --- | --- | --- | --- |
| `SMTP_PASSWORD` | Secret | the app-specific password | the App Password |
| `SMTP_HOST` | Variable | `smtp.mail.me.com` | `smtp.gmail.com` |
| `SMTP_PORT` | Variable | `587` | `587` |
| `SMTP_USERNAME` | Variable | your iCloud address | your Gmail address |
| `EMAIL_TO` | Variable | where to send it; commas for several | same |
| `EMAIL_FROM` | Variable, optional | defaults to `SMTP_USERNAME` | same |

To preview the email locally without sending: `python3 -m literature_brief.mailer --brief site/brief.json --preview email.html`.
