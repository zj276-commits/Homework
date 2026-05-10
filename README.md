# Homework 3 — AI Report Validation System

Course template supporting **[`HOMEWORK3.md`](../HOMEWORK3.md)**: an AI-as-reviewer **custom rubric** (not the LAB Likert set), three generator prompts, replicated runs, and a complete statistical workup (ANOVA + Welch *t*-tests with Bonferroni + Cohen's *d* effect sizes).

Python **3.10–3.13** all work for this homework (no `crewai`). The other course folders (`agentpy/`, `agentr/`) need `crewai` which today caps at `<3.14`, so 3.12 is a safe lab-wide default if you only want one venv.

---

## Quick start

```text
cd 11_decision_support/homework3
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env       # then edit and set OLLAMA_API_KEY
.\.venv\Scripts\python run_experiment.py
.\.venv\Scripts\python analyze_experiment.py
.\.venv\Scripts\python build_submission_docx.py
```

Open `HOMEWORK3_submission.docx` in Word; write Section 1 yourself (≈500 words, **not AI-generated**), paste your screenshots, swap repo links for your fork, save, and upload to Canvas.

**Offline smoke test** (no API bill, useful for development):

```text
.\.venv\Scripts\python run_experiment.py --mock
.\.venv\Scripts\python analyze_experiment.py
.\.venv\Scripts\python build_submission_docx.py
```

**Validate your existing Homework 1 / 2 reports** instead of generating new briefings:

```text
.\.venv\Scripts\python run_experiment.py --reports-dir path\to\your\hw1or2\reports
```

Reports whose filenames start with `A_minimal_paragraph`, `B_structured_sections`, or `C_ICS_traceability` are grouped by prompt; others are kept as `other` and excluded from the A/B/C ANOVA.

---

## What the system does

1. **Grounding pack** — [`data/scenario_source.md`](data/scenario_source.md) lists **F1–F10** synthetic facts for a fictional full-scale exercise (Exercise Riverdale). Generators and validators see the *same* table, so the validator can grade traceability and fidelity against a fixed ground truth.
2. **Generation (3 prompts)**
   - **Prompt A** — single 120–200-word paragraph, no headings (minimal structure).
   - **Prompt B** — four fixed sections: Situation / Current actions / Risks / Coordination.
   - **Prompt C** — ICS-style sections + every operational claim tagged with `(F#)` IDs (`UNKNOWN` if unsourced).
3. **Validation** — a second Ollama call scores each report as **strict JSON** on five custom dimensions, plus free-text `validator_notes`.
4. **Statistics** — [`analyze_experiment.py`](analyze_experiment.py) computes:
   - One-way ANOVA on `composite_score` (primary endpoint) **and** on each of the 5 rubric dimensions (secondary)
   - Pairwise Welch *t*-tests with Bonferroni correction
   - Cohen's *d* effect sizes (pooled SD) for every pair
   - A boxplot of composite scores and a grouped bar chart of per-dimension means

Outputs (under `output/`):

| File | Role |
|------|------|
| `reports/*.txt` | Generated briefings (one file per replicate) |
| `validation_results.csv` | One row per `(prompt × replicate)`, all scores + notes |
| `per_dimension_summary.csv` | Mean / SD per prompt per dimension + F & p_value |
| `stats_summary.txt` | Human-readable ANOVA + pairwise + verdict |
| `boxplot_composite.png` | Distribution of composite_score by prompt |
| `bar_per_dimension.png` | Mean per dimension, side-by-side by prompt |
| `run_metadata.json` | Mode (live/mock), model names, replicate count, timestamp |

---

## Validation rubric (customized — not LAB Likert)

| Dimension | Scale | Contrast with LAB Likert (`09_text_analysis/02_ai_quality_control.R`) |
|-----------|-------|-----------------------------------------------------------------------|
| `scenario_fidelity_pct` | 0–100 % | LAB scores generic *accuracy* (1–5 Likert); here it is graded against a labeled fact pack (F1–F10) — a continuous coverage metric, not a vibe. |
| `decision_actionability_pct` | 0–100 % | LAB has no operational-usability axis. We score whether a coordinator can derive next-hour actions from the briefing alone. |
| `briefing_structure_score` | 0–10 integer | LAB measures *clarity / formality / succinctness* with separate Likerts; we collapse those into one EOC-scannable layout score. |
| `traceability_pct` | 0–100 % | New axis. LAB does not measure whether claims trace to specific source IDs. |
| `overreach_severity` | 0–4 integer (penalty) | LAB has a single boolean `accurate`. We grade *degrees* of out-of-scope harm (real casualty claims, fake URLs, scope contradictions). |

**Composite** = equal-weighted mean of the four normalized positive dimensions (`briefing_structure_score × 10` to share a 0–100 scale). `overreach_severity` is reported and tested **separately** so the design choices are auditable; if you want a single number that punishes overreach, swap in your own formula in `compute_composite()` and document the tradeoff in your write-up.

---

## Experimental design (defaults)

- **Factor:** generator prompt (`A_minimal_paragraph`, `B_structured_sections`, `C_ICS_traceability`).
- **Replicates per prompt:** `HW3_REPLICATES_PER_PROMPT` (default **6**) → **18 briefings** generated, **18 validations** scored.
- **Sample temperatures (decreasing diversity, A→B→C):** 0.85 / 0.55 / 0.35. Override with `HW3_GENERATION_TEMPERATURE_A|B|C`.
- **Hypothesis (default):** *H₀: mean composite score is equal across prompts. H₁: at least one differs.*
- **Test plan:** one-way ANOVA → if significant, Bonferroni-corrected pairwise Welch *t*-tests. Report Cohen's *d* alongside *p*.

---

## Canvas deliverable (.docx)

[`build_submission_docx.py`](build_submission_docx.py) writes **`HOMEWORK3_submission.docx`** with:

- Rubric table (Section 3)
- Auto-embedded `stats_summary.txt` (Section 4)
- Auto-embedded `per_dimension_summary.csv` as a table (Section 4a)
- Both figures auto-embedded (Section 5)
- Sample report text + first rows of `validation_results.csv` (Section 6)
- Run metadata + usage block (Section 7)
- Screenshot checklist (Section 8)
- A clearly marked **Section 1** with an italic "must be written by the student, NOT AI" placeholder you replace before upload.

---

## Git links for graders

Submission repo: **<https://github.com/zj276-commits/Homework>**

- Experiment driver: <https://github.com/zj276-commits/Homework/blob/main/run_experiment.py>
- Analysis: <https://github.com/zj276-commits/Homework/blob/main/analyze_experiment.py>
- Rubric / scenario: <https://github.com/zj276-commits/Homework/blob/main/data/scenario_source.md>
- Sample outputs: <https://github.com/zj276-commits/Homework/blob/main/output/validation_results.csv>
- Per-dimension summary: <https://github.com/zj276-commits/Homework/blob/main/output/per_dimension_summary.csv>
- Documentation: <https://github.com/zj276-commits/Homework/blob/main/README.md>

---

## Troubleshooting

- **HTTP 401 / 429 from Ollama Cloud** — confirm `OLLAMA_API_KEY`, reduce `--replicates`, or increase the inter-call sleep in `run_experiment.py`.
- **Validator returns non-JSON** — the JSON-extraction regex tolerates the most common cases; if it still fails, lower validator temperature (already 0.15) or switch `OLLAMA_MODEL_VALIDATOR` to a more JSON-reliable model.
- **ANOVA p = NaN** — all scores identical inside at least one group. Raise replicates and/or temperatures.
- **`build_submission_docx.py` complains about missing files** — re-run `analyze_experiment.py` first.

---

← [`HOMEWORK3.md`](../HOMEWORK3.md)
