# analyze_experiment.py
# Homework 3 — full statistical workup on validation_results.csv:
#   * One-way ANOVA on composite_score (primary) and on each rubric dimension (secondary)
#   * Pairwise Welch t-tests with Bonferroni correction
#   * Cohen's d effect sizes (pooled SD) for each pair
#   * Best-prompt verdict + per-dimension mean table
#   * Boxplot (composite) and grouped bar chart (per-dimension means)
#
# Usage:
#   python analyze_experiment.py
#   python analyze_experiment.py --csv output/validation_results.csv

from __future__ import annotations

import argparse
import math
from itertools import combinations
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

PROMPT_ORDER = ["A_minimal_paragraph", "B_structured_sections", "C_ICS_traceability"]
RUBRIC_DIMENSIONS = [
    "scenario_fidelity_pct",
    "decision_actionability_pct",
    "briefing_structure_score",
    "traceability_pct",
]
ALL_DIMENSIONS = RUBRIC_DIMENSIONS + ["overreach_severity", "composite_score"]


def _hw3_root() -> Path:
    return Path(__file__).resolve().parent


def _normalize_for_bar(name: str, value: float) -> float:
    """Same normalization as run_experiment.compute_composite — for plotting only."""
    if name == "briefing_structure_score":
        return max(0.0, min(10.0, float(value))) * 10.0
    if name == "overreach_severity":
        return max(0.0, min(4.0, float(value))) * 25.0  # 0–100 for plot only
    return max(0.0, min(100.0, float(value)))


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    """Pooled-SD Cohen's d. Returns NaN if both groups are constant."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return float("nan")
    return float((a.mean() - b.mean()) / math.sqrt(pooled))


def anova_block(df: pd.DataFrame, dim: str) -> tuple[list[np.ndarray], dict]:
    groups = [df.loc[df["prompt_key"] == k, dim].dropna().values for k in PROMPT_ORDER]
    if any(len(g) < 2 for g in groups):
        return groups, {"F": float("nan"), "p": float("nan")}
    f_stat, p_val = stats.f_oneway(*groups)
    return groups, {"F": float(f_stat), "p": float(p_val)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, default=None)
    args = parser.parse_args()

    root = _hw3_root()
    csv_path = Path(args.csv) if args.csv else root / "output" / "validation_results.csv"
    if not csv_path.is_file():
        raise SystemExit(f"Missing {csv_path}. Run: python run_experiment.py --mock")

    df = pd.read_csv(csv_path)
    # Drop rows from external '--reports-dir' that don't belong to A/B/C
    df = df[df["prompt_key"].isin(PROMPT_ORDER)].copy()
    if df.empty:
        raise SystemExit("CSV has no A/B/C rows to analyze.")

    out_dir = root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    pretty = {k: k.replace("_", " ") for k in PROMPT_ORDER}

    # ---------- primary: composite_score ----------
    groups_c, anova_c = anova_block(df, "composite_score")
    n_per = [len(g) for g in groups_c]
    means_c = [float(g.mean()) for g in groups_c]
    stds_c = [float(g.std(ddof=1)) if len(g) > 1 else 0.0 for g in groups_c]
    best_idx = int(np.argmax(means_c))
    best_prompt = PROMPT_ORDER[best_idx]

    lines: list[str] = [
        "Homework 3 — Statistical analysis of AI report validation experiment",
        "=" * 72,
        f"Source CSV: {csv_path.relative_to(root) if csv_path.is_relative_to(root) else csv_path}",
        f"N per prompt: {dict(zip(PROMPT_ORDER, n_per))}    (total N = {sum(n_per)})",
        "",
        "=== PRIMARY ENDPOINT: composite_score (equal-weight mean of 4 normalized positive dims) ===",
        f"Means: " + ", ".join(f"{pretty[k]}={m:.2f}" for k, m in zip(PROMPT_ORDER, means_c)),
        f"SD:    " + ", ".join(f"{pretty[k]}={s:.2f}" for k, s in zip(PROMPT_ORDER, stds_c)),
        f"One-way ANOVA: F = {anova_c['F']:.4f},  p = {anova_c['p']:.6g}",
        "",
        "Pairwise Welch t-tests (two-sided) on composite_score:",
    ]

    pairs = list(combinations(range(len(PROMPT_ORDER)), 2))
    raw_ps: list[float] = []
    d_values: list[float] = []
    for i, j in pairs:
        if len(groups_c[i]) >= 2 and len(groups_c[j]) >= 2:
            t = stats.ttest_ind(groups_c[i], groups_c[j], equal_var=False)
            d = cohens_d(groups_c[i], groups_c[j])
        else:
            t = type("T", (), {"statistic": float("nan"), "pvalue": float("nan")})()
            d = float("nan")
        raw_ps.append(float(t.pvalue))
        d_values.append(float(d))
        lines.append(
            f"  {pretty[PROMPT_ORDER[i]]} vs {pretty[PROMPT_ORDER[j]]}: "
            f"t={t.statistic:.3f}, p={t.pvalue:.6g}, Cohen's d={d:.3f}"
        )

    m = len(pairs)
    lines.append("")
    lines.append(f"Bonferroni-adjusted alpha: 0.05 / {m} = {0.05 / m:.5f}")
    sig_pairs: list[str] = []
    for k, (i, j) in enumerate(pairs):
        pc = min(1.0, raw_ps[k] * m) if not math.isnan(raw_ps[k]) else float("nan")
        sig = "**significant**" if (not math.isnan(pc) and pc < 0.05) else "not significant"
        if sig.startswith("**"):
            sig_pairs.append(f"{pretty[PROMPT_ORDER[i]]} ≠ {pretty[PROMPT_ORDER[j]]}")
        lines.append(
            f"  {pretty[PROMPT_ORDER[i]]} vs {pretty[PROMPT_ORDER[j]]}: p_adj ≈ {pc:.6g}  ({sig})"
        )

    lines.append("")
    lines.append("=== Verdict ===")
    if not math.isnan(anova_c["p"]) and anova_c["p"] < 0.05:
        lines.append(
            f"Reject H0 (p_anova = {anova_c['p']:.4g} < 0.05). "
            f"Highest mean composite score: **{pretty[best_prompt]}** ({means_c[best_idx]:.2f})."
        )
        if sig_pairs:
            lines.append("Significantly different pairs (after Bonferroni): " + "; ".join(sig_pairs))
    else:
        lines.append(
            f"Cannot reject H0 (p_anova = {anova_c['p']:.4g}). "
            "Differences between prompts are not statistically significant at alpha=0.05."
        )

    # ---------- secondary: per-dimension ANOVA ----------
    lines.append("")
    lines.append("=== SECONDARY: per-dimension one-way ANOVA ===")
    lines.append(f"{'dimension':<32} {'F':>10} {'p':>14} {'best (highest mean)':<30}")
    per_dim_results: dict[str, dict] = {}
    for dim in RUBRIC_DIMENSIONS + ["overreach_severity"]:
        groups, anova = anova_block(df, dim)
        if dim == "overreach_severity":
            # lower is better — flip best-direction
            means_dim = [float(g.mean()) for g in groups]
            best = PROMPT_ORDER[int(np.argmin(means_dim))]
            best_note = f"{pretty[best]} (lowest mean = best)"
        else:
            means_dim = [float(g.mean()) for g in groups]
            best = PROMPT_ORDER[int(np.argmax(means_dim))]
            best_note = pretty[best]
        per_dim_results[dim] = {"F": anova["F"], "p": anova["p"], "best": best, "means": means_dim}
        lines.append(f"{dim:<32} {anova['F']:>10.3f} {anova['p']:>14.6g} {best_note:<30}")

    out_txt = out_dir / "stats_summary.txt"
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_txt.read_text(encoding="utf-8"))

    # ---------- per-dimension table CSV (useful for the .docx table) ----------
    table_rows = []
    for dim in RUBRIC_DIMENSIONS + ["overreach_severity", "composite_score"]:
        row = {"dimension": dim}
        for k in PROMPT_ORDER:
            grp = df.loc[df["prompt_key"] == k, dim].dropna()
            row[f"{k}_mean"] = round(float(grp.mean()), 3) if len(grp) else None
            row[f"{k}_sd"] = round(float(grp.std(ddof=1)), 3) if len(grp) > 1 else None
        if dim in per_dim_results:
            row["F"] = round(per_dim_results[dim]["F"], 3) if not math.isnan(per_dim_results[dim]["F"]) else None
            row["p_value"] = per_dim_results[dim]["p"]
        elif dim == "composite_score":
            row["F"] = round(anova_c["F"], 3)
            row["p_value"] = anova_c["p"]
        table_rows.append(row)
    table_df = pd.DataFrame(table_rows)
    table_df.to_csv(out_dir / "per_dimension_summary.csv", index=False)

    # ---------- boxplot of composite ----------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.boxplot(
        [df.loc[df["prompt_key"] == k, "composite_score"].dropna().values for k in PROMPT_ORDER],
        showmeans=True,
    )
    ax.set_xticks([1, 2, 3])
    ax.set_xticklabels(["Prompt A\n(minimal)", "Prompt B\n(structured)", "Prompt C\n(ICS+F-ids)"])
    ax.set_ylabel("Composite score (0–100)")
    ax.set_title("Composite validation score by prompt (Homework 3)")
    ax.set_ylim(0, 100)
    fig.tight_layout()
    box_png = out_dir / "boxplot_composite.png"
    fig.savefig(box_png, dpi=150)
    plt.close(fig)

    # ---------- grouped bar chart of per-dimension means (0–100 scale) ----------
    dims_for_bar = RUBRIC_DIMENSIONS + ["overreach_severity"]
    width = 0.25
    x = np.arange(len(dims_for_bar))
    fig2, ax2 = plt.subplots(figsize=(10, 5))
    for idx, k in enumerate(PROMPT_ORDER):
        means = [
            _normalize_for_bar(d, df.loc[df["prompt_key"] == k, d].dropna().mean())
            for d in dims_for_bar
        ]
        ax2.bar(x + idx * width - width, means, width, label=k.replace("_", " "))
    ax2.set_xticks(x)
    ax2.set_xticklabels(
        ["fidelity", "actionability", "structure ×10", "traceability", "overreach ×25 (low=good)"],
        rotation=15,
        ha="right",
    )
    ax2.set_ylabel("Mean score (normalized 0–100)")
    ax2.set_title("Per-dimension comparison across prompts")
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper right", fontsize=9)
    fig2.tight_layout()
    bar_png = out_dir / "bar_per_dimension.png"
    fig2.savefig(bar_png, dpi=150)
    plt.close(fig2)

    print(f"\nWrote {box_png}")
    print(f"Wrote {bar_png}")
    print(f"Wrote {out_dir / 'per_dimension_summary.csv'}")
    print(f"Wrote {out_txt}")


if __name__ == "__main__":
    main()
