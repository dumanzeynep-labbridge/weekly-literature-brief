# The Bioprocess Brief

A Python project that discovers new papers, rates their relevance to your research, writes a standalone `index.html`, and publishes it with GitHub Actions and GitHub Pages every **Monday at 07:00 UTC**.

The configured interests are bioprocessing, mammalian cell engineering, metabolic engineering, CHO production systems, and technologies applicable to cell and gene therapy manufacturing. Both **OpenAI** and **Anthropic** are supported. Python 3.11 or newer is required; there are **no external Python dependencies**.

## Set up GitHub in a few steps

1. Create a GitHub repository and upload **the contents of this project folder** into its root. Include the hidden `.github` directory. The workflow must end up at `.github/workflows/workflow.yml`, alongside the root `config.json` and `literature_brief/` directory.
2. Go to **Settings → Secrets and variables → Actions → Secrets**. Add one selected provider's key:
   - OpenAI, the default: `OPENAI_API_KEY`.
   - Anthropic: `ANTHROPIC_API_KEY`. Under **Variables**, also add `LLM_PROVIDER` with value `anthropic`.
3. Optionally add Actions variables `LLM_MODEL` to select a compatible model and `CONTACT_EMAIL` for polite metadata API requests. With no model override, the defaults are `gpt-4.1-mini` for OpenAI and `claude-haiku-4-5` for Anthropic.
4. Go to **Settings → Pages → Build and deployment → Source** and select **GitHub Actions**. Choose a public repository for the simplest Pages setup; private-repository Pages availability depends on your GitHub plan.
5. Commit the workflow on your repository's **default branch**, then open **Actions → Weekly literature brief → Run workflow**. Select the default branch and run it once. After a successful deployment, the `github-pages` environment links to your brief.

The workflow then runs weekly with `cron: "0 7 * * 1"`. It uses GitHub's deployment artifact flow, so you do not need a `gh-pages` branch or a personal access token. Pull requests run offline tests without API calls or deployment. Manual runs on other branches do not deploy.

GitHub schedules can start late under load, especially at the top of the hour, and public repositories' schedules can be disabled after 60 days without repository activity. Check Actions if an expected edition is missing. See [GitHub scheduling behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) and [Pages workflow setup](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Preview and run locally

Open `preview/index.html` for the included **fictional demonstration**. Its sample papers, authors, findings, and scores are synthetic; no paper or LLM API was called to create it.

From this project directory, using Python 3.11+:

```bash
# Rebuild a demo without credentials or network access.
python3 -m literature_brief --demo --output preview

# Fetch real metadata without making paid LLM calls.
python3 -m literature_brief --fetch-only --output site

# Build a real scored brief with OpenAI.
export OPENAI_API_KEY='YOUR_API_KEY'
python3 -m literature_brief --output site

# Alternatively, use Anthropic; changing provider selects its default model.
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY='YOUR_API_KEY'
python3 -m literature_brief --output site

# Optional: override the model for your selected provider.
export LLM_MODEL='YOUR_COMPATIBLE_MODEL_ID'

# Reproduce a publication window ending at the start of a given UTC date.
python3 -m literature_brief --end-date 2026-10-19 --output site

# Run the offline test suite.
python3 -m unittest discover -s tests -v
```

API keys are read from environment variables only. `.env.example` documents the settings, but `.env` files are **not loaded automatically**. Never put keys in `config.json`, generated files, or Git commits.

The normal output is `site/index.html` plus `site/brief.json` and `.nojekyll`. `brief.json` contains all retained metadata and assessment statuses, including papers below the relevance threshold, without abstracts, or excluded from scoring by an optional cap. The Pages workflow uploads only `site/`.

## Sources and coverage

Discovery uses documented metadata APIs rather than scraping publisher websites:

| Source | Retrieval |
| --- | --- |
| bioRxiv | Direct API, full date-range pagination; first versions only |
| Nature Portfolio | Crossref DOI prefix `10.1038` |
| PLOS journals | Crossref DOI prefix `10.1371` |
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

Nature and PLOS coverage is determined by the configured DOI prefixes, not a live complete publisher catalog. In particular, journals using other DOI prefixes need their own ISSN entry. Metadata deposits and indexing can be delayed, so this is a discovery brief, not guaranteed exhaustive journal coverage. Strict seven-day windows can miss papers indexed after their publication week. Use `--end-date` to rerun a past window if needed.

To add a journal, append its registered ISSN to `sources.crossref` in `config.json`:

```json
{"name": "Your journal", "issn": "1234-567X"}
```

This ISSN is a format example, not a real configured journal. Use a verified print or electronic ISSN. A source entry must specify exactly one `issn` or `prefix`. Sources are fully paginated; there is no hidden discovery cap. DOI deduplication removes repeated metadata records. A preprint and its final journal article generally have different DOIs and remain separate records, with preprints clearly marked.

## Dates, assessment, and display

The window is **seven complete UTC calendar days**, ending at midnight at the start of the run date. For a Monday run, it covers the previous Monday through Sunday. For example, `--end-date 2026-10-19` covers October 12–18 inclusive. Monday morning publications appear in the following edition. Most APIs expose day-level dates, so the project does not imply exact timestamp precision.

The earliest known online or print publication day is used. Crossref deposit, creation, and indexing dates are never substituted for publication dates. Imprecise earlier dates are excluded and reported as partial coverage. bioRxiv revisions of older papers are excluded.

Every discovered paper with an available abstract is scored by default. No keyword prefilter excludes papers before assessment. The model returns a validated object with:

- `relevance`: integer from 1 to 10, based on the interests in `config.json`.
- `methodology`: the first summary bullet, grounded in the abstract.
- `key_findings`: the second summary bullet, grounded in the abstract.
- `rationale`: a short explanation of topic fit and material transferability limits.

Scores 9–10 mean direct relevance to mammalian/CHO production or cell and gene therapy manufacturing; 7–8 indicate clearly relevant enabling work; 5–6 indicate a plausible indirect application; 1–4 indicate limited connection. The prompt distinguishes reviews from experiments and treats microbial-to-mammalian transfer cautiously. Paper text is treated as source data, never as instructions. Scores measure topic fit, not scientific quality.

The HTML shows the top 20 papers scoring at least 6 by default, ordered by relevance and then publication date. Other assessed and unscored papers remain accessible in expandable groups. Search, publication-type filtering, expandable abstracts, full author lists when available, and JSON download are included. The page uses local CSS and JavaScript and remains readable with JavaScript disabled.

## Adjust volume and API use

Edit `config.json`:

| Setting | Default | Effect |
| --- | --- | --- |
| `interests` | Your detailed research topics | Used in scoring and cache invalidation |
| `interest_labels` | Short topic labels | Display only |
| `min_relevance` | `6` | Minimum score for the featured/relevant groups |
| `top_n` | `20` | Featured cards; `0` shows all relevant papers up front |
| `max_papers` | `0` | Maximum papers sent for assessment; `0` scores all available abstracts |
| `llm.provider` | `openai` | `openai` or `anthropic` |
| `llm.model` | `gpt-4.1-mini` | A compatible model; change it with the provider or leave empty for its default |
| `llm.workers` | `3` | Concurrent assessments; lower this if your account has tight rate limits |
| `llm.max_output_tokens` | `1000` | Maximum output tokens per assessment |
| `sources.request_interval_seconds` | `0.15` | Pause between sequential metadata requests |

bioRxiv and the broad Nature/PLOS prefixes can return thousands of papers in a week. The default makes one LLM request for each uncached paper with an abstract, so API use depends on volume and model. `top_n` controls display only. Set `max_papers` to limit assessment requests; the newest papers are then assessed first, the remaining metadata stays in the brief, and a visible notice identifies the excluded count. It is a request cap, not a monetary budget; retries may issue additional requests.

Assessments are cached by metadata, abstract, research interests, provider, model, token limit, and prompt version. Repeat runs can reuse matching results. GitHub caches are opportunistic and may expire, so cache availability is not guaranteed. The cache contains no API keys and is not deployed to Pages. HTTP retries are bounded and use backoff for rate limits and transient failures.

## Failure behavior and implementation

Individual source or paper failures remain visible in the generated brief. A total discovery failure, or failure to assess any eligible paper, returns a nonzero exit code and prevents the workflow from replacing the previous Pages edition. Local diagnostic output is still written when the report can be assembled. A successful empty search is shown differently from a failed search. Partial success can publish with visible coverage or assessment notices.

The workflow allows up to 180 minutes for discovery and scoring. A timeout stops deployment; reduce scope or concurrent workload as appropriate. Keys are available only to the generation step. The build job has read access to repository/Pages metadata; only the deployment job receives Pages write and OIDC permissions.

```text
.github/workflows/workflow.yml   Weekly schedule, offline tests, cache, Pages deployment
config.json                      Topics, model, journals, display settings
literature_brief/sources.py       bioRxiv, Crossref, Europe PMC, dates, deduplication
literature_brief/scoring.py       Provider adapters, prompt, validation, cache
literature_brief/pipeline.py      Assessment orchestration and failure decisions
literature_brief/render.py        Standalone HTML and JSON export
preview/index.html               Fictional offline demonstration
tests/                           Mocked tests; no keys or network needed
```

API contracts were checked against the official [bioRxiv API](https://api.biorxiv.org/), [Crossref filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/), [Crossref pagination guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/), [Europe PMC REST API](https://europepmc.org/RestfulWebService), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), and [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

The supplied preview and tests run without credentials. A live LLM run and GitHub deployment still require your selected API key and repository setup above.
