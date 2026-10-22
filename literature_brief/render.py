"""Render a portable, accessible literature brief with no external assets."""

from __future__ import annotations

from datetime import date, datetime
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit


def _text(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _safe_url(value: object) -> str | None:
    """Accept ordinary public web links, never script/data/credential URLs."""
    url = str(value or "").strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        # Accessing port also validates malformed port declarations.
        _ = parsed.port
    except ValueError:
        return None
    return url


def _date_label(value: object) -> str:
    raw = str(value or "")
    try:
        return date.fromisoformat(raw[:10]).strftime("%d %b %Y")
    except ValueError:
        return raw or "Date unavailable"


def _score(paper: dict) -> int | None:
    value = paper.get("relevance")
    if paper.get("status") != "scored" or isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 10:
        return value
    return None


def _personal_score(paper: dict) -> float | None:
    value = paper.get("personal_score")
    return float(value) if type(value) in {int, float} else None


def _personal_reasons(paper: dict) -> str:
    reasons = paper.get("personal_reasons") or []
    if not reasons:
        return ""
    items = "".join(f"<li>{_text(reason)}</li>" for reason in reasons)
    return f'<div class="personal-reasons"><strong>Why it is in your brief</strong><ul>{items}</ul></div>'


def select_personal(report: dict) -> tuple[list[dict], list[dict]]:
    """Split papers into this week's personal picks and everything else."""
    papers = list(report.get("papers") or [])
    settings = report.get("personal") or {}
    ordered = sorted(papers, key=lambda paper: (_personal_score(paper) or 0, str(paper.get("published") or "")),
                     reverse=True)
    minimum = float(settings.get("min_score", 4))
    top_n = int(settings.get("top_n", 10))
    picks = [paper for paper in ordered if (_personal_score(paper) or 0) >= minimum]
    picks = picks[:top_n] if top_n > 0 else picks
    chosen = {id(paper) for paper in picks}
    return picks, [paper for paper in ordered if id(paper) not in chosen]


def select_featured(report: dict) -> list[dict]:
    """AI mode: papers at the AI threshold first, then the free-ranking picks.

    A strict model or a failed (e.g. rate-limited) request therefore never hides
    a strong personal match such as a watched author's paper.
    """
    papers = list(report.get("papers") or [])
    min_relevance = int(report.get("min_relevance", 6))
    top_n = int(report.get("top_n", 20))
    eligible = sorted((paper for paper in papers if (_score(paper) or 0) >= min_relevance),
                      key=lambda paper: (_score(paper), _personal_score(paper) or 0, str(paper.get("published") or "")),
                      reverse=True)
    featured = eligible[:top_n] if top_n > 0 else eligible
    if report.get("personal"):
        chosen = {id(paper) for paper in featured}
        featured = featured + [paper for paper in select_personal(report)[0] if id(paper) not in chosen]
    return featured


def _focused_match_details(paper: dict, focused_searches: dict) -> str:
    match = paper.get("focused_search") or {}
    blocks = {block["id"]: block.get("label", block["id"]) for block in focused_searches.get("blocks", [])}
    combinations = {combo["id"]: combo.get("label", combo["id"]) for combo in focused_searches.get("combinations", [])}
    matched_blocks = match.get("matched_blocks") or {}
    notes = match.get("ambiguity_notes") or []
    if not matched_blocks and not notes:
        return ""
    labels = [combinations.get(combo, combo) for combo in match.get("matched_combinations", [])]
    label_html = (
        '<p><strong>Focused combinations:</strong> ' + _text("; ".join(labels)) + '</p>'
        if labels else '<p><strong>Keyword blocks only:</strong> no complete focused combination matched.</p>'
    )
    terms_html = ''.join(
        f'<li><strong>{_text(blocks.get(block, block))}</strong>: {_text("; ".join(terms))}</li>'
        for block, terms in matched_blocks.items()
    )
    notes_html = ''.join(f'<p class="terminology-note">{_text(note)}</p>' for note in notes)
    return ('<div class="focused-match">' + label_html +
            (f'<details><summary>Matched terms in title / abstract</summary><ul>{terms_html}</ul></details>' if terms_html else '') +
            notes_html + '</div>')


def _paper_card(paper: dict, rank: int | None, demo: bool, *, anchor: str = "",
                focused_searches: dict | None = None, discovery_only: bool = False) -> str:
    score = None if discovery_only else _score(paper)
    title = _text(paper.get("title") or "Untitled paper")
    url = None if demo else _safe_url(paper.get("url"))
    title_html = (
        f'<a href="{_text(url)}" target="_blank" rel="noopener noreferrer">{title}'
        '<span class="external" aria-hidden="true"> ↗</span></a>'
        if url else title
    )
    authors = paper.get("authors") or []
    authors_text = ", ".join(str(author) for author in authors) if isinstance(authors, list) else str(authors)
    journal = str(paper.get("journal") or paper.get("source") or "Source unavailable")
    source = str(paper.get("source") or journal)
    publication = "preprint" if paper.get("is_preprint") else "journal"
    badge = '<span class="tag preprint">Preprint · not peer reviewed</span>' if paper.get("is_preprint") else ""
    if demo:
        badge += '<span class="tag demo-tag">Synthetic example</span>'
    if paper.get("source") == "OpenAlex citation alert":
        badge += '<span class="tag citation-tag">Cites your seed paper</span>'
    personal = _personal_score(paper)
    if score is not None:
        score_html = f'<div class="score" aria-label="Relevance {score} out of 10"><strong>{score}</strong><span>/10 relevance</span></div>'
    elif personal:
        score_html = (f'<div class="score personal-score" aria-label="Personal match score {personal:g}">'
                      f'<strong>{personal:g}</strong><span>match points</span></div>')
    else:
        score_html = '<span class="tag unscored-tag">' + ('Metadata only' if discovery_only else 'Unscored') + '</span>'
    rank_html = f'<span class="rank" aria-label="Rank {rank}">{rank:02d}</span>' if rank is not None else ""
    if score is not None:
        body = (
            '<ul class="findings">'
            f'<li><strong>Methodology</strong><span>{_text(paper.get("methodology") or "Not described in the available abstract.")}</span></li>'
            f'<li><strong>Findings</strong><span>{_text(paper.get("key_findings") or "Not described in the available abstract.")}</span></li>'
            '</ul>'
            f'<p class="rationale"><strong>Why it matters</strong> {_text(paper.get("rationale") or "No relevance explanation supplied.")}</p>'
        )
    else:
        status = paper.get("status")
        explanation = {
            "missing_abstract": "An abstract was unavailable, so this paper has not been rated or summarized.",
            "capped": "This paper was not assessed because the configured assessment limit was reached. The original metadata remains available below.",
            "not_scored": "This paper was retrieved for discovery only; no AI assessment was requested.",
            "scoring_failed": "The relevance assessment could not be completed. The original metadata remains available below.",
        }.get(status, "This paper has not been assessed. The original metadata remains available below.")
        if discovery_only:
            explanation = "This paper was retrieved for discovery only; no AI assessment was requested."
        body = "" if paper.get("personal_reasons") else f'<p class="unscored-reason">{explanation}</p>'
    body += _personal_reasons(paper)
    abstract = str(paper.get("abstract") or "").strip()
    abstract_html = (
        '<details class="abstract"><summary>Read abstract</summary>'
        f'<p>{_text(abstract)}</p></details>' if abstract else
        '<p class="abstract-unavailable">Abstract unavailable from the source.</p>'
    )
    doi = paper.get("doi")
    doi_html = f'<span>DOI: {_text(doi)}</span>' if doi else ""
    origin = "Synthetic example · paper links disabled" if demo else f"Retrieved via {source}"
    search_text = " ".join([str(paper.get("title") or ""), authors_text, journal, abstract,
                            str(paper.get("methodology") or ""), str(paper.get("key_findings") or "")])
    match = paper.get("focused_search") or {}
    combinations = " ".join(match.get("matched_combinations") or [])
    focused_html = _focused_match_details(paper, focused_searches or {})
    return f'''<article class="paper" id="{_text(anchor)}" data-search="{_text(search_text.lower())}" data-kind="{publication}" data-combinations="{_text(combinations)}">
  <header class="paper-header"><div class="paper-heading">{rank_html}<div>
    <div class="paper-meta"><span>{_text(journal)}</span><span aria-hidden="true">·</span><time>{_text(_date_label(paper.get("published")))}</time>{badge}</div>
    <h3>{title_html}</h3><p class="authors">{_text(authors_text or "Authors unavailable")}</p>
  </div></div>{score_html}</header>
  {body}{focused_html}{abstract_html}
  <footer class="paper-footer"><span>{_text(origin)}</span>{doi_html}</footer>
</article>'''


def _focused_search_section(summary: dict, papers: list[dict], anchors: dict[int, str]) -> str:
    combinations = summary.get("combinations") or []
    if not combinations:
        return ""
    rows = []
    for combination in combinations:
        matched = [paper for paper in papers if combination["id"] in
                   (paper.get("focused_search") or {}).get("matched_combinations", [])]
        paper_links = ''.join(
            f'<li><a class="focused-paper-link" href="#{_text(anchors[id(paper)])}">{_text(paper.get("title") or "Untitled paper")}</a>'
            + (' <span class="missing-abstract-note">(title match; abstract unavailable)</span>' if not str(paper.get("abstract") or "").strip() else '')
            + '</li>' for paper in matched
        )
        results = (f'<ul class="focused-paper-list">{paper_links}</ul>' if matched else
                   '<p>No matches found in the titles and available abstracts of the configured sources for this publication window.</p>')
        rows.append(
            '<details class="focused-combination"' + (' open' if matched else '') + '><summary>'
            f'{_text(combination.get("label") or combination["id"])} <span>{len(matched)}</span></summary>{results}</details>'
        )
    return (f'<section class="focused-searches" aria-labelledby="focused-heading"><div class="section-heading"><div>'
            '<p class="eyebrow">Your keyword combinations</p><h2 id="focused-heading">Focused search matches</h2></div>'
            f'<p>{_text(summary.get("matched_papers", 0))} distinct paper(s) matched</p></div>'
            '<p>Each combination requires at least one term from every named block. Terms within a block are alternatives. '
            'Matches use titles and available abstracts from this week’s configured sources; they do not establish a paper’s scientific conclusions. '
            'All discovered papers remain below, including those without a focused match.</p>'
            '<p>NRF1 / NRF-1 can also refer to NFE2L1. GABPA is distinct from NFE2L2 (often called NRF2). '
            'Check the paper’s terminology; notes flag some ambiguous uses on the paper card.</p>'
            + ''.join(rows) + '</section>')


def _source_rows(statuses: list[dict]) -> str:
    rows = []
    for source in statuses:
        status = source.get("status", "error")
        css = status if status in {"ok", "partial", "error"} else "error"
        label = {"ok": "Available", "partial": "Partial", "error": "Unavailable"}.get(status, "Unavailable")
        rows.append(
            f'<tr><th scope="row">{_text(source.get("name") or "Unnamed source")}</th>'
            f'<td><span class="source-state {css}">{label}</span></td>'
            f'<td>{_text(source.get("fetched", 0))}</td>'
            f'<td>{_text(source.get("message") or "—")}</td></tr>'
        )
    return "\n".join(rows) or '<tr><td colspan="4">No source retrieval information was recorded.</td></tr>'


def render_brief(report: dict, output_dir: Path) -> Path:
    """Write index.html and a downloadable brief.json; return the HTML path.

    Every supplied paper stays available, including lower-ranked and unscored
    records. A standalone page remains fully usable when JavaScript is disabled.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    papers = list(report.get("papers") or [])
    min_relevance = int(report.get("min_relevance", 6))
    top_n = int(report.get("top_n", 20))
    demo = bool(report.get("demo", False))
    fetch_only = bool(report.get("fetch_only", False))
    focused_searches = report.get("focused_searches") or {}
    anchors = {id(paper): f"paper-{index}" for index, paper in enumerate(papers, 1)}

    def card(paper: dict, rank: int | None = None) -> str:
        return _paper_card(paper, rank, demo, anchor=anchors[id(paper)], focused_searches=focused_searches,
                           discovery_only=fetch_only)

    def focused_group_open(group: list[dict]) -> str:
        return ' open' if any((paper.get("focused_search") or {}).get("matched_combinations") for paper in group) else ''

    personal = report.get("personal") or None
    ranked_free = fetch_only and personal is not None
    scored = [] if fetch_only else sorted(
        (paper for paper in papers if _score(paper) is not None),
        key=lambda paper: (_score(paper), _personal_score(paper) or 0, str(paper.get("published") or "")), reverse=True,
    )
    eligible = [paper for paper in scored if _score(paper) >= min_relevance]
    lower = [paper for paper in scored if _score(paper) < min_relevance]
    picks, others = select_personal(report) if ranked_free else ([], [])
    if ranked_free:
        featured = picks
    else:
        featured = papers if fetch_only else select_featured(report)
    shown = {id(paper) for paper in featured}
    remaining = [] if fetch_only else [paper for paper in eligible if id(paper) not in shown]
    lower = [paper for paper in lower if id(paper) not in shown]
    unscored = papers if fetch_only else [paper for paper in papers if _score(paper) is None and id(paper) not in shown]
    featured_html = "\n".join(card(paper, rank if ranked_free or not fetch_only else None) for rank, paper in enumerate(featured, 1))
    if not featured:
        if ranked_free and papers:
            message = (f"No paper reached {personal.get('min_score', 4):g} match points this week. The closest papers are listed "
                       "first under “Other papers this week”. Adding seed papers or watched authors in config.json widens what counts as a match.")
            empty_heading = "No strong personal matches this week"
        elif fetch_only:
            message = "No papers were returned for this publication window. Check source availability below; an empty brief does not establish that no relevant work was published."
            empty_heading = "No papers discovered this week"
        else:
            message = (
                "Papers were retrieved, but none has a completed assessment at the selected relevance threshold. Browse the other records below or download the full brief."
                if papers else
                "No papers were returned for this publication window. Check source availability below; an empty brief does not establish that no relevant work was published."
            )
            empty_heading = "No featured papers this week"
        featured_html = f'<div class="empty-state"><h3>{empty_heading}</h3><p>{message}</p></div>'
    groups = []
    if remaining:
        cards = "\n".join(card(paper, rank) for rank, paper in enumerate(remaining, len(featured) + 1))
        groups.append(f'<details class="paper-group"{focused_group_open(remaining)}><summary>More relevant papers <span>{len(remaining)}</span></summary><div class="group-content">{cards}</div></details>')
    if lower:
        cards = "\n".join(card(paper) for paper in lower)
        groups.append(f'<details class="paper-group"{focused_group_open(lower)}><summary>Below the relevance threshold <span>{len(lower)}</span></summary><div class="group-content">{cards}</div></details>')
    if others:
        cards = "\n".join(card(paper) for paper in others)
        groups.append(f'<details class="paper-group"><summary>Other papers this week, closest first <span>{len(others)}</span></summary><div class="group-content">{cards}</div></details>')
    if unscored and not fetch_only:
        cards = "\n".join(card(paper) for paper in unscored)
        group_heading = "Discovered papers" if fetch_only else "Papers awaiting assessment"
        groups.append(f'<details class="paper-group"{focused_group_open(unscored)}><summary>{group_heading} <span>{len(unscored)}</span></summary><div class="group-content">{cards}</div></details>')
    interests = "".join(f'<span class="interest">{_text(interest)}</span>' for interest in report.get("interest_labels", report.get("interests", [])))
    title = _text(report.get("title") or "The weekly bioengineering brief")
    generated_at = str(report.get("generated_at") or "")
    try:
        generated_label = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).strftime("%d %b %Y · %H:%M %Z")
    except ValueError:
        generated_label = generated_at or "Not recorded"
    stats = report.get("stats") or {}
    discovered = stats.get("discovered", len(papers))
    source_statuses = report.get("source_statuses") or []
    problems = [source for source in source_statuses if source.get("status") != "ok"]
    alert = (
        '<div class="notice partial-notice"><strong>Coverage is incomplete.</strong> '
        f'{len(problems)} source(s) reported unavailable or partial results. See retrieval details below.</div>'
        if problems else ""
    )
    scoring_failed = max(int(stats.get("scoring_failed", 0)), sum(paper.get("status") == "scoring_failed" for paper in papers))
    if scoring_failed and not fetch_only:
        alert += ('<div class="notice partial-notice"><strong>Some AI assessments are incomplete.</strong> '
                  f'{scoring_failed} paper(s) could not be assessed. Their metadata and available abstracts remain accessible below.</div>')
    if ranked_free:
        alert = ('<div class="notice discovery-notice"><strong>Free personal ranking; no AI was used.</strong> Papers are ranked by your keyword weights, '
                 'watched authors, and seed papers. Each card lists exactly why it scored.</div>') + alert
    elif fetch_only:
        alert = '<div class="notice discovery-notice"><strong>Discovery only; no AI assessment was requested.</strong> This run includes source metadata and available abstracts, without relevance scores or AI summaries.</div>' + alert
    if demo:
        alert = '<div class="notice demo-notice"><strong>SYNTHETIC DEMO</strong> These fictional papers and assessments illustrate the layout. They are not literature findings. Paper links are disabled.</div>' + alert
    capped = stats.get("capped", 0)
    cap_notice = (
        f'<p><strong>Assessment cap:</strong> {_text(capped)} paper(s) were not sent for LLM assessment because the configured limit was reached. Those records remain in this brief.</p>'
        if capped and not fetch_only else ""
    )
    assessment_note = (
        "This discovery-only brief includes source metadata and available abstracts. No relevance scores or AI summaries were requested. Preprints have not been peer reviewed."
        if fetch_only else
        "Relevance scores and the two-bullet summaries are AI-generated from abstracts, not full texts. Verify claims against the linked paper. A high score reflects topic fit, not study quality or clinical readiness. Preprints have not been peer reviewed."
    )
    papers_heading = "Papers discovered this week" if fetch_only else "This week's top papers"
    ranking_note = "Source metadata &amp; available abstracts" if fetch_only else "Ranked by relevance · newest first on ties"
    if personal is not None and not fetch_only:
        ranking_note = f"AI relevance {min_relevance}+ first, then your top personal matches"
    if ranked_free:
        papers_heading = "Your top matches this week"
        ranking_note = "Ranked by personal match points · newest first on ties"
        assessment_note = ("Match points come from transparent rules in config.json: weighted keyword blocks, focused combinations, "
                           "watched authors, papers citing your seed papers, and text similarity to those seed papers. "
                           "No AI was used, so there are no AI summaries. A high score reflects topic fit, not study quality. "
                           "Preprints have not been peer reviewed.")
    focused_html = _focused_search_section(focused_searches, papers, anchors)
    focused_options = ''.join(
        f'<option value="{_text(combo["id"])}">{_text(combo.get("label") or combo["id"])}</option>'
        for combo in focused_searches.get("combinations", [])
    )
    focused_filter = (
        '<div><label for="paper-combination">Focused combination</label><select id="paper-combination">'
        '<option value="all">All papers</option>' + focused_options + '</select></div>' if focused_options else ''
    )
    if fetch_only:
        abstracts_available = sum(bool(str(paper.get("abstract") or "").strip()) for paper in papers)
        overview_items = [(discovered, "papers discovered"), (abstracts_available, "abstracts available"),
                          (focused_searches.get("matched_papers", 0), "focused matches"),
                          (len(papers) - abstracts_available, "abstracts unavailable")]
        provenance = "Discovery only · no LLM used"
        download_label = "Complete metadata &amp; keyword matches (JSON)"
        intro = "The week’s papers and keyword matches for bioprocessing and cell engineering."
        if ranked_free:
            overview_items[1] = (len(picks), f"matches at {personal.get('min_score', 4):g}+ points")
            provenance = "Free personal ranking · no LLM used"
            intro = "This week’s research, ranked for your project by keywords, watched authors, and seed papers."
    else:
        overview_items = [(discovered, "papers discovered"), (len(scored), "assessed"),
                          (len(eligible), f"at relevance {min_relevance}+"), (len(unscored), "unscored")]
        provenance = f'{report.get("provider") or "Provider not recorded"} / {report.get("model") or "Model not recorded"}'
        download_label = "Complete metadata &amp; assessments (JSON)"
        intro = "The week's research, selected for bioprocessing and cell engineering."
    overview_html = ''.join(f'<div><strong>{_text(value)}</strong><span>{_text(label)}</span></div>' for value, label in overview_items)
    document = f'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="A weekly literature brief on bioprocessing, cell engineering, and biotechnology.">
<meta name="color-scheme" content="light"><title>{title}</title><style>{_CSS}</style></head>
<body><a class="skip-link" href="#papers">Skip to papers</a>
<div class="page"><header class="masthead"><a class="wordmark" href="#">LAB <span>literature intelligence</span></a><a class="download" href="brief.json" download>Download full brief <span aria-hidden="true">↓</span></a></header>
<main><section class="hero" aria-labelledby="brief-title"><p class="eyebrow">Weekly research digest</p>
<h1 id="brief-title">{title}</h1><p class="intro">{_text(intro)}</p>
<p class="window">Publication window <strong>{_text(_date_label(report.get("window_start")))}</strong> to <strong>{_text(_date_label(report.get("window_end")))}</strong> <span>(end exclusive · UTC)</span></p>
<div class="interests" aria-label="Research interests">{interests}</div>
</section>{alert}
<section class="overview" aria-label="Brief statistics">{overview_html}</section>
{"" if ranked_free else focused_html}
<section id="papers" aria-labelledby="papers-heading"><div class="section-heading"><div><p class="eyebrow">Read first</p><h2 id="papers-heading">{papers_heading}</h2></div><p>{ranking_note}</p></div>
<form class="filters" role="search" hidden><div><label for="paper-search">Search this brief</label><input id="paper-search" type="search" placeholder="Title, author, journal, or keyword" autocomplete="off"></div><div><label for="paper-kind">Publication type</label><select id="paper-kind"><option value="all">All publications</option><option value="journal">Journal articles</option><option value="preprint">Preprints</option></select></div>{focused_filter}<p id="filter-count" role="status" aria-live="polite"></p></form>
<div class="featured-papers">{featured_html}</div><p id="no-matches" class="empty-state" hidden>No papers match these filters. Try another search, publication type, or focused combination.</p>
{"".join(groups)}</section>
{focused_html if ranked_free else ""}
<section class="coverage" aria-labelledby="coverage-heading"><p class="eyebrow">Behind the brief</p><h2 id="coverage-heading">Coverage &amp; interpretation</h2>
<p>{assessment_note}</p>
<p>Availability depends on source indexing, publication dates, abstract access, and configured retrieval limits. Online-first dates can differ across sources; delayed indexing may omit recently published papers. This brief is a discovery aid, not an exhaustive systematic review.</p>{cap_notice}
<details class="source-details"><summary>Source retrieval details <span>{len(source_statuses)} retrieval steps</span></summary><div class="table-wrap"><table><caption class="sr-only">Source availability and paper counts</caption><thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Fetched</th><th scope="col">Notes</th></tr></thead><tbody>{_source_rows(source_statuses)}</tbody></table></div></details>
<p class="provenance">Generated {_text(generated_label)} · {_text(provenance)}</p></section>
</main><footer class="site-footer"><span>LAB · A little less searching. A little more discovery.</span><a href="brief.json" download>{download_label}</a></footer></div>
<script>{_JS}</script></body></html>'''
    html_path = output_dir / "index.html"
    html_path.write_text(document, encoding="utf-8")
    (output_dir / "brief.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return html_path


_CSS = """
:root{--paper:#f7f5ef;--white:#fffefa;--ink:#202f31;--muted:#607072;--teal:#136c61;--line:#dcded5;--wash:#e9efea;--serif:Georgia,'Times New Roman',serif;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink);background:var(--paper);font-size:16px;line-height:1.6}*{box-sizing:border-box}body{margin:0}a{color:var(--teal);text-underline-offset:4px}a:hover{text-decoration-thickness:2px}button,input,select{font:inherit}a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #c17d37;outline-offset:4px}[hidden]{display:none!important}.page{max-width:1180px;padding:0 48px;margin:auto}.masthead{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:27px 0;border-bottom:1px solid var(--ink)}.wordmark{font-weight:800;letter-spacing:.13em;text-decoration:none;color:var(--ink);font-size:19px}.wordmark span{font-weight:400;font-size:11px;letter-spacing:.16em;margin-left:15px;text-transform:uppercase}.download{font-size:12px;font-weight:650;white-space:nowrap;text-decoration:none}.download span{margin-left:8px}.hero{padding:58px 0 31px}.eyebrow{text-transform:uppercase;letter-spacing:.17em;font-size:10px;font-weight:750;color:var(--teal);margin:0 0 15px}h1,h2,h3{font-family:var(--serif);font-weight:400;line-height:1.16;letter-spacing:-.025em}h1{font-size:clamp(36px,5.2vw,62px);max-width:900px;margin:0 0 19px}h2{font-size:32px;margin:0}h3{font-size:26px;margin:10px 0 11px;line-height:1.28}h3 a{color:var(--ink);text-decoration:none}h3 a:hover{color:var(--teal);text-decoration:underline;text-decoration-thickness:1px}.external{font-size:.64em;color:var(--teal);white-space:nowrap}.intro{font-size:17px;color:var(--muted);margin:0 0 20px}.window{font-size:12px;color:var(--muted);margin:0 0 20px}.window strong{font-weight:600;color:var(--ink)}.window span{font-size:11px}.interests{display:flex;gap:7px;flex-wrap:wrap}.interest{border:1px solid var(--line);border-radius:3px;font-size:10px;padding:4px 9px;color:var(--muted);background:#ffffff40}.overview{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:0 0 48px;padding:24px 0}.overview>div{padding:0 24px;border-left:1px solid var(--line)}.overview>div:first-child{padding-left:0;border:0}.overview strong{font:400 34px/1 var(--serif);display:block;margin-bottom:9px}.overview span{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}.notice{font-size:13px;padding:16px 20px;margin:0 0 24px;border-left:3px solid #bf8642;background:#f2ead8}.demo-notice{background:#f8e3d3;border-color:#b65025}.notice strong{margin-right:5px}.section-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:24px}.section-heading .eyebrow{margin-bottom:10px}.section-heading>p{font-size:10px;color:var(--muted);margin:0 0 5px}.filters{display:flex;align-items:flex-end;gap:15px;margin:0 0 22px;padding:15px 18px;background:var(--wash);border-radius:4px}.filters>div:first-child{flex:1}.filters label{display:block;font-size:10px;font-weight:650;margin-bottom:5px}.filters input,.filters select{border:1px solid #c5cfc8;background:var(--white);border-radius:3px;color:var(--ink);padding:8px 10px;font-size:12px;width:100%;min-height:37px}.filters>p{font-size:10px;color:var(--muted);min-width:95px;margin:0 0 9px}.paper{padding:29px 31px 21px;border:1px solid var(--line);border-radius:4px;background:var(--white);margin:0 0 18px;box-shadow:0 2px 5px #20302f03}.paper-header{display:flex;align-items:flex-start;justify-content:space-between;gap:25px}.paper-heading{display:flex;gap:20px;min-width:0}.rank{font:400 22px/1.1 var(--serif);color:#9aa6a0;min-width:27px;padding-top:4px}.paper-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px;font-size:10px;line-height:1.7;color:var(--muted);letter-spacing:.01em}.paper-meta>span:first-child{font-weight:650;color:var(--teal)}.tag{font-size:9px;font-weight:650;letter-spacing:.015em;line-height:1.5;border-radius:3px;padding:3px 7px;background:var(--wash);white-space:nowrap}.preprint{background:#f4eddd;color:#806020}.demo-tag{background:#fae6d6;color:#974a24}.unscored-tag{background:#eff0ec;color:var(--muted);margin-top:1px}.score{background:var(--wash);color:var(--teal);border-radius:4px;text-align:center;padding:11px 10px 10px;min-width:88px;flex-shrink:0}.score strong{display:block;font:400 32px/1 var(--serif);margin-bottom:4px}.score span{display:block;font-size:9px;white-space:nowrap}.authors{font-size:11px;color:var(--muted);margin:0 0 21px;overflow-wrap:anywhere}.findings{list-style:none;padding:0;margin:0 0 20px;display:grid;gap:13px}.findings li{display:grid;grid-template-columns:100px 1fr;gap:15px;font-size:13px}.findings li>strong{font-size:10px;letter-spacing:.04em;text-transform:uppercase;padding-top:2px;color:var(--teal)}.rationale{font-size:12px;color:var(--muted);margin:0 0 18px;padding:14px 17px;background:#f5f6f0;border-left:2px solid #adc9bf;line-height:1.65}.rationale strong{color:var(--ink);font-weight:650;margin-right:4px}.abstract summary{font-size:11px;color:var(--teal);font-weight:650;cursor:pointer;display:list-item}.abstract>p{font-size:12px;color:var(--muted);white-space:pre-line;margin:12px 0 0;line-height:1.8}.paper-footer{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;font-size:9px;color:var(--muted);border-top:1px solid #e9eae3;padding-top:13px;margin-top:19px;overflow-wrap:anywhere}.abstract-unavailable{font-size:11px;color:var(--muted);font-style:italic;margin:0}.unscored-reason{font-size:13px;color:var(--muted);margin:0 0 15px}.paper-group{border-top:1px solid var(--line);margin-top:28px}.paper-group>summary{cursor:pointer;padding:21px 0;font-family:var(--serif);font-size:21px}.paper-group>summary>span{font:500 11px system-ui,sans-serif;color:var(--teal);background:var(--wash);border-radius:20px;padding:3px 8px;margin-left:10px}.group-content{padding-top:1px}.empty-state{padding:32px;border:1px dashed #bdc8bd;border-radius:4px;color:var(--muted);font-size:13px;margin:0 0 20px}.empty-state h3{font-size:24px;color:var(--ink);margin-top:0}.empty-state p{max-width:700px}.coverage{border-top:1px solid var(--ink);margin-top:54px;padding:33px 0 27px}.coverage h2{font-size:28px;margin-bottom:19px}.coverage>p:not(.eyebrow){max-width:900px;color:var(--muted);font-size:12px;line-height:1.8}.source-details{margin-top:24px;border:1px solid var(--line);border-radius:4px}.source-details>summary{cursor:pointer;font-size:12px;font-weight:650;padding:14px 18px}.source-details>summary span{font-size:10px;font-weight:400;color:var(--muted);margin-left:12px}.table-wrap{overflow-x:auto;padding:0 18px 15px}table{width:100%;border-collapse:collapse;font-size:10px;text-align:left}th,td{vertical-align:top;padding:10px 12px 10px 0;border-top:1px solid var(--line)}thead th{text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-size:9px}tbody th{font-weight:600;min-width:115px}td:last-child{min-width:180px}.source-state{font-weight:600;white-space:nowrap}.source-state.ok{color:var(--teal)}.source-state.partial{color:#86601c}.source-state.error{color:#a24d31}.coverage .provenance{font-size:10px;margin-top:23px}.site-footer{display:flex;justify-content:space-between;gap:20px;font-size:10px;color:var(--muted);padding:22px 0 30px;border-top:1px solid var(--line)}.site-footer a{color:var(--muted)}.skip-link{position:absolute;top:-100px;left:10px;background:var(--white);padding:12px;z-index:2}.skip-link:focus{top:10px}.sr-only{position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.focused-searches{margin-bottom:44px;padding-bottom:28px;border-bottom:1px solid var(--line)}.focused-searches>p{font-size:12px;color:var(--muted);max-width:900px}.focused-combination{margin-top:10px;border:1px solid var(--line);border-radius:4px;background:var(--white)}.focused-combination>summary{font-size:12px;font-weight:650;cursor:pointer;padding:12px 16px}.focused-combination>summary>span{font-size:10px;color:var(--teal);background:var(--wash);border-radius:20px;padding:2px 8px;margin-left:8px}.focused-combination>p,.focused-paper-list{font-size:12px;color:var(--muted);margin:0;padding:0 16px 14px 34px}.focused-paper-list li{margin:4px 0}.missing-abstract-note{font-size:10px}.focused-match{margin-bottom:18px;padding:12px 15px;background:#edf3ee;border-left:2px solid #adc9bf;font-size:11px}.focused-match p{margin:0 0 7px}.focused-match p:last-child{margin-bottom:0}.focused-match summary{cursor:pointer;color:var(--teal)}.focused-match ul{padding-left:18px;margin:7px 0}.focused-match .terminology-note{color:#806020;margin-top:8px}.filters{flex-wrap:wrap}.filters>div{min-width:140px}.filters>div:first-child{min-width:190px}.filters select{max-width:300px}.paper:target{outline:2px solid var(--teal);outline-offset:4px;scroll-margin-top:20px}.personal-reasons{margin:0 0 18px;padding:13px 16px;background:#f5f6f0;border-left:2px solid var(--teal);font-size:12px}.personal-reasons strong{font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--teal)}.personal-reasons ul{margin:6px 0 0;padding-left:18px}.personal-reasons li{margin:2px 0}.personal-score{background:#eef2ea}#papers+.focused-searches{margin:44px 0 0;padding-top:28px;border-top:1px solid var(--line);border-bottom:0}.citation-tag{background:#e3eef7;color:#245b80}
@media(max-width:760px){.page{padding:0 23px}.masthead{padding:22px 0}.wordmark span{display:none}.hero{padding-top:38px}h1{font-size:40px}.intro{font-size:15px}.overview{margin-bottom:33px;padding:20px 0;gap:15px 0;grid-template-columns:repeat(2,1fr)}.overview>div{padding-left:20px}.overview>div:nth-child(3){border:0;padding-left:0}.overview strong{font-size:30px}.overview span{font-size:9px}.section-heading{display:block}.section-heading>p{margin-top:12px}.filters{flex-wrap:wrap}.filters>div:first-child{flex-basis:100%}.filters>div:nth-child(2){flex:1}.filters>p{min-width:0}.paper{padding:22px 20px 18px}.paper-header{gap:15px}.paper-heading{gap:10px}.rank{min-width:20px;font-size:18px}h3{font-size:23px}.score{min-width:63px;padding:10px 6px}.score strong{font-size:28px}.score span{font-size:8px}.findings li{grid-template-columns:1fr;gap:4px}.paper-meta{font-size:9px}.paper-meta .tag{font-size:8px}.authors{font-size:10px}.site-footer{flex-direction:column;gap:8px}.source-details>summary span{display:block;margin:3px 0 0 15px}}
@media(max-width:420px){.page{padding:0 16px}.download{font-size:10px}.paper{padding:19px 15px}.paper-header{position:relative;padding-top:0}.rank{display:none}.score{min-width:55px}.score span{font-size:7px}.paper-meta{gap:4px}h3{font-size:21px}.window span{display:block;margin-top:4px}}
@media(prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
@media print{body{background:white}.page{max-width:none;padding:0}.filters,.download,.site-footer a,.skip-link{display:none!important}.paper{box-shadow:none;break-inside:avoid}.paper-group{display:block}.hero{padding-top:25px}.coverage{margin-top:30px}h1{font-size:36px}h3{font-size:21px}}
"""


_JS = """
(() => {
  const form = document.querySelector('.filters');
  const search = document.getElementById('paper-search');
  const kind = document.getElementById('paper-kind');
  const combination = document.getElementById('paper-combination');
  const cards = Array.from(document.querySelectorAll('.paper'));
  if (!form || !cards.length) return;
  form.hidden = false;
  form.addEventListener('submit', event => event.preventDefault());
  const update = () => {
    const query = search.value.trim().toLocaleLowerCase();
    const focused = combination ? combination.value : 'all';
    const active = Boolean(query) || kind.value !== 'all' || focused !== 'all';
    let count = 0;
    cards.forEach(card => {
      const visible = (!query || card.dataset.search.includes(query)) &&
        (kind.value === 'all' || card.dataset.kind === kind.value) &&
        (focused === 'all' || card.dataset.combinations.split(' ').includes(focused));
      card.hidden = !visible;
      if (visible) count += 1;
    });
    document.querySelectorAll('.paper-group').forEach(group => {
      const matches = Array.from(group.querySelectorAll('.paper')).some(card => !card.hidden);
      group.hidden = active && !matches;
      if (active && matches) group.open = true;
    });
    document.getElementById('filter-count').textContent = `${count} of ${cards.length} papers`;
    document.getElementById('no-matches').hidden = count !== 0;
  };
  search.addEventListener('input', update);
  kind.addEventListener('change', update);
  if (combination) combination.addEventListener('change', update);
  document.querySelectorAll('.focused-paper-link').forEach(link => {
    link.addEventListener('click', () => {
      search.value = '';
      kind.value = 'all';
      if (combination) combination.value = 'all';
      update();
      const card = document.getElementById(link.hash.slice(1));
      const group = card && card.closest('.paper-group');
      if (group) group.open = true;
    });
  });
  update();
})();
"""
