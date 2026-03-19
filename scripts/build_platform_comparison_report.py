from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _platform_snapshot() -> dict:
    youtube = _load_json(REPORTS / "youtube_test_10_summary_20260313.json")
    instagram = _load_json(REPORTS / "instagram_test_10_summary_20260313.json")
    instagram_wrap = _load_json(REPORTS / "instagram_phase2_wrap_20260313.json")
    tiktok = _load_json(REPORTS / "tiktok_test_20_summary_20260313.json")

    return {
        "youtube": {
            "benchmark_file": str((REPORTS / "youtube_test_10_summary_20260313.json").resolve()),
            "sample_size": youtube["completed"],
            "avg_score": youtube["avg_score"],
            "median_score": youtube["median_score"],
            "avg_tokens": youtube["avg_tokens"],
            "median_tokens": youtube["median_tokens"],
            "levels": youtube["levels"],
            "status": "working but auth-limited",
            "summary": "Completes reliably, but rich media/transcript extraction is still constrained by YouTube anti-bot friction.",
        },
        "instagram": {
            "benchmark_file": str((REPORTS / "instagram_test_10_summary_20260313.json").resolve()),
            "sample_size": instagram["completed"],
            "avg_score": instagram["avg_score"],
            "median_score": instagram["median_score"],
            "avg_tokens": instagram["avg_tokens"],
            "median_tokens": instagram["median_tokens"],
            "levels": instagram["levels"],
            "status": "strongest current platform",
            "summary": "Worker browser ingest is live, and authenticated media/ASR/OCR is proven on the wrapped validation path.",
            "authenticated_validation": instagram_wrap["final_validation_job"],
        },
        "tiktok": {
            "benchmark_file": str((REPORTS / "tiktok_test_20_summary_20260313.json").resolve()),
            "sample_size": tiktok["count"],
            "avg_score": tiktok["avg_score"],
            "median_score": tiktok["median_score"],
            "avg_tokens": tiktok["avg_tokens"],
            "median_tokens": tiktok["median_tokens"],
            "levels": tiktok["levels"],
            "avg_asr_tokens": tiktok["avg_asr_tokens"],
            "status": "good baseline, media path still needs hardening",
            "summary": "Queue-backed ingest is stable and evidence is materially better than YouTube, but ASR is not contributing yet.",
            "best_case": tiktok["best"],
            "worst_case": tiktok["worst"],
            "manipulation_outliers": tiktok.get("manipulation_outliers", []),
        },
    }


def build_report() -> dict:
    platforms = _platform_snapshot()
    ranking = sorted(
        (
            {
                "platform": name,
                "avg_tokens": snapshot["avg_tokens"],
                "avg_score": snapshot["avg_score"],
                "status": snapshot["status"],
            }
            for name, snapshot in platforms.items()
        ),
        key=lambda item: item["avg_tokens"],
        reverse=True,
    )
    return {
        "date": "2026-03-13",
        "basis": {
            "youtube": "10-link live benchmark after final auth hardening pass",
            "instagram": "10-link benchmark plus authenticated Phase 2 validation job",
            "tiktok": "20-link live benchmark",
        },
        "platforms": platforms,
        "rank_by_evidence_density": ranking,
        "overall_read": {
            "strongest_now": "instagram",
            "second": "tiktok",
            "third": "youtube",
            "key_constraint": "TikTok and YouTube still need stronger media/audio extraction. Instagram already cleared that bar with authenticated worker ingest.",
        },
        "next_steps": [
            "Finish TikTok media URL hardening and re-run the 100-link CSV benchmark to measure ASR uptake.",
            "Keep YouTube on the current worker-backed path but treat it as the weakest evidence source until auth improves.",
            "Use this report as the baseline comparison while expanding TikTok coverage.",
        ],
    }


def main() -> int:
    report = build_report()
    out = REPORTS / "platform_comparison_report_20260313.json"
    out.write_text(json.dumps(report, indent=2))
    print(out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
