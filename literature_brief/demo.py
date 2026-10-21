"""Clearly labelled fictional content for an offline presentation preview."""

from datetime import date, datetime, timedelta, timezone

from .pipeline import publication_window
from .search import evaluate_paper, summarize_searches


def demo_report(config: dict, end: date | None = None) -> dict:
    start, end = publication_window(end)
    examples = [
        ("A model-guided approach to balancing growth and productivity in CHO cultures",
         "Biotechnology and Bioengineering", 10,
         "The fictional study combines extracellular metabolite profiling with a constrained metabolic model to compare feeding strategies in CHO cultures.",
         "The synthetic example illustrates a feeding strategy that balances viable cell growth and recombinant protein production; no real experimental claim is made."),
        ("Engineering producer-cell metabolism for more consistent vector manufacturing",
        "Molecular Therapy Advances", 9,
         "The fictional study compares engineered mammalian producer cells using small-scale process runs and measurements of metabolic state and vector quality.",
         "This illustrative result describes reduced process variability, with scale-up and product-specific validation still required."),
        ("A tunable perfusion platform for rapid media screening",
         "bioRxiv", 8,
         "The fictional preprint uses parallel miniature perfusion cultures and online process measurements to screen media formulations.",
         "This synthetic example highlights how screening could prioritize media candidates for subsequent confirmation in production processes."),
    ]
    papers = []
    for index, (title, journal, relevance, methodology, findings) in enumerate(examples):
        papers.append({
            "title": f"[Fictional example] {title}", "authors": ["Example Researcher", "Demo Author"],
            "url": "", "doi": "", "abstract": f"FICTIONAL DEMONSTRATION. {methodology} {findings}",
            "journal": journal, "published": (end - timedelta(days=index + 1)).isoformat(),
            "source": "Offline demonstration", "is_preprint": journal == "bioRxiv",
            "relevance": relevance, "methodology": methodology, "key_findings": findings,
            "rationale": "Illustrative relevance score for the configured research interests. No LLM or paper API was called.",
            "status": "scored", "error": "",
        })
        papers[-1]["focused_search"] = evaluate_paper(papers[-1]["title"], papers[-1]["abstract"], config.get("search"))
    return {
        "title": config["title"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(), "window_end": end.isoformat(), "interests": config["interests"],
        "interest_labels": config.get("interest_labels", config["interests"]),
        "provider": config["llm"]["provider"], "model": config["llm"]["model"], "demo": True,
        "min_relevance": config["min_relevance"], "top_n": config["top_n"], "papers": papers,
        "focused_searches": summarize_searches(papers, config.get("search")),
        "source_statuses": [{"name": "Offline demonstration", "status": "ok", "fetched": len(papers),
                             "message": "Synthetic examples only; no live sources or LLM calls."}],
        "stats": {"discovered": len(papers), "scored": len(papers), "missing_abstract": 0,
                  "scoring_failed": 0, "capped": 0},
    }
