# run_experiment.py
# Homework 3 — generate drill briefings with 3 prompts, validate with a custom rubric (Ollama Cloud).
# Course pattern: httpx + /api/chat + JSON format for validator. Pairs with analyze_experiment.py.
#
# Usage (from this folder, after pip install -r requirements.txt and setting .env):
#   python run_experiment.py                           # 3 prompts * 6 replicates = 18 API generations + 18 validations
#   python run_experiment.py --mock                    # no API; deterministic synthetic CSV for testing
#   python run_experiment.py --replicates 8
#   python run_experiment.py --reports-dir ../reports  # validate existing reports (Homework 1/2 outputs)

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv

PROMPT_KEYS = ["A_minimal_paragraph", "B_structured_sections", "C_ICS_traceability"]
PROMPT_LABELS = {
    "A_minimal_paragraph": "Prompt A — minimal paragraph",
    "B_structured_sections": "Prompt B — structured sections",
    "C_ICS_traceability": "Prompt C — ICS traceability",
}
RUBRIC_DIMENSIONS = [
    "scenario_fidelity_pct",
    "decision_actionability_pct",
    "briefing_structure_score",
    "traceability_pct",
]


def _hw3_root() -> Path:
    return Path(__file__).resolve().parent


def _load_scenario() -> str:
    p = _hw3_root() / "data" / "scenario_source.md"
    if not p.is_file():
        raise FileNotFoundError(f"Missing scenario file: {p}")
    return p.read_text(encoding="utf-8")


def ollama_chat(
    base_url: str,
    api_key: str | None,
    model: str,
    messages: list[dict],
    format_json: bool = False,
    temperature: float | None = None,
    timeout: float = 180.0,
) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    body: dict = {"model": model, "messages": messages, "stream": False}
    if format_json:
        body["format"] = "json"
    if temperature is not None:
        body["options"] = {"temperature": float(temperature)}
    headers = {"Content-Type": "application/json"}
    ak = (api_key or "").strip()
    if ak:
        headers["Authorization"] = f"Bearer {ak}"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message") or {}
    content = msg.get("content", "")
    return content if isinstance(content, str) else str(content)


def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object found in model output")
    return json.loads(m.group(0))


def generation_messages(scenario_table: str, prompt_key: str) -> list[dict]:
    system = (
        "You are an emergency management duty officer drafting a short operational briefing "
        "for internal partners. Use only information consistent with the SOURCE FACTS table. "
        "Do not invent real-world incidents, casualties, or media URLs."
    )

    user_a = f"""SOURCE FACTS (markdown table — use only these facts; do not cite URLs):
{scenario_table}

TASK: Write **one** cohesive paragraph (120–200 words) summarizing what leaders need to know right now. Do **not** use section headings or bullet lists."""

    user_b = f"""SOURCE FACTS:
{scenario_table}

TASK: Write a briefing using **exactly** these markdown sections and headings (in order):
## Situation
## Current actions
## Risks / unknowns
## Coordination
Keep each section 2–4 sentences. Total 180–260 words."""

    user_c = f"""SOURCE FACTS:
{scenario_table}

TASK: Produce an **ICS-style** briefing. Every operational statement must tag the fact ID(s) it rests on, e.g. (F2)(F4). If you cannot tie a sentence to any F-id, write that sentence as **UNKNOWN — needs source**.
Use headings: Situation / Objectives / Current actions / Comms / Public info / Logistics gaps.
Target 220–320 words."""

    user_map = {
        "A_minimal_paragraph": user_a,
        "B_structured_sections": user_b,
        "C_ICS_traceability": user_c,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_map[prompt_key]},
    ]


def validator_prompt(report_text: str, scenario_table: str) -> str:
    return f"""You are a **validation engineer** scoring synthetic drill briefings against a fixed fact pack.

SOURCE FACTS (ground truth — same as given to the author):
{scenario_table}

REPORT TO SCORE:
---
{report_text}
---

Score the report. Use a **custom rubric** (not generic Likert writing quality):
- **scenario_fidelity_pct** (0–100): How completely the narrative reflects the fact IDs without contradicting them.
- **decision_actionability_pct** (0–100): Can a coordinator decide **next-hour** actions (who does what / comms / constraints)?
- **briefing_structure_score** (0–10): Organized for EOC use (sections, scanning, not one wall of text). 0 = unstructured blob.
- **traceability_pct** (0–100): Can a reader see **which facts** support claims (explicit F-tags, quotes of IDs, or equivalent)?
- **overreach_severity** (0–4 integer): 0 = no false real-world escalation; 4 = claims real mass casualty / real hazmat / fake URLs / contradiction of facts.

Return **only** valid JSON with keys:
scenario_fidelity_pct, decision_actionability_pct, briefing_structure_score, traceability_pct, overreach_severity, validator_notes
Use integers where specified. Keep validator_notes under 40 words."""


def normalize_dim(name: str, value: float) -> float:
    """Map each positive dimension to 0–100 so they can be averaged."""
    v = float(value)
    if name == "briefing_structure_score":
        v = max(0.0, min(10.0, v)) * 10.0
    else:
        v = max(0.0, min(100.0, v))
    return v


def compute_composite(scores: dict) -> float:
    """Simple equal-weight mean of the 4 normalized positive dimensions (0–100).
    overreach_severity is reported separately, not folded into the composite.
    """
    parts = [normalize_dim(d, scores.get(d, 0.0)) for d in RUBRIC_DIMENSIONS]
    return round(sum(parts) / len(parts), 4)


def _row_from_scores(
    prompt_key: str, replicate: int, scores: dict, notes: str, report_rel_path: str
) -> dict:
    row = {
        "run_id": str(uuid.uuid4()),
        "prompt_key": prompt_key,
        "prompt_label": PROMPT_LABELS[prompt_key],
        "replicate": replicate,
        **{d: round(float(scores.get(d, 0.0)), 2) for d in RUBRIC_DIMENSIONS},
        "overreach_severity": int(scores.get("overreach_severity", 0)),
        "composite_score": compute_composite(scores),
        "validator_notes": (notes or "")[:500],
        "report_rel_path": report_rel_path,
    }
    return row


def run_mock(output_dir: Path, replicates: int) -> Path:
    """Deterministic synthetic data with realistic noise; C edges out B, both beat A."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260510)
    rows: list[dict] = []
    profiles = {
        # (fidelity, action, structure /10, traceability, overreach_bias)
        "A_minimal_paragraph": (62, 55, 3.2, 38, 0.9),
        "B_structured_sections": (74, 70, 6.8, 60, 0.3),
        "C_ICS_traceability": (78, 73, 7.1, 82, 0.2),
    }
    for pk in PROMPT_KEYS:
        mu_f, mu_a, mu_s, mu_t, over_bias = profiles[pk]
        for rep in range(1, replicates + 1):
            scores = {
                "scenario_fidelity_pct": max(0, min(100, rng.gauss(mu_f, 8))),
                "decision_actionability_pct": max(0, min(100, rng.gauss(mu_a, 9))),
                "briefing_structure_score": max(0, min(10, rng.gauss(mu_s, 1.2))),
                "traceability_pct": max(0, min(100, rng.gauss(mu_t, 10))),
                "overreach_severity": min(4, max(0, int(round(rng.gauss(over_bias, 0.6))))),
            }
            rel = f"reports/MOCK_{pk}_r{rep}.txt"
            rep_path = output_dir / rel
            rep_path.parent.mkdir(parents=True, exist_ok=True)
            rep_path.write_text(
                f"[MOCK REPORT {pk} replicate {rep}]\nPlaceholder for offline testing.\n",
                encoding="utf-8",
            )
            rows.append(
                _row_from_scores(
                    pk, rep, scores, "MOCK row — synthetic data for offline testing.", rel
                )
            )

    csv_path = output_dir / "validation_results.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    _write_metadata(
        output_dir,
        mode="mock",
        replicates=replicates,
        model_gen="MOCK",
        model_val="MOCK",
        n_rows=len(rows),
    )
    print(f"Wrote {csv_path} ({len(rows)} rows) [MOCK]")
    return csv_path


def _write_metadata(
    output_dir: Path,
    mode: str,
    replicates: int,
    model_gen: str,
    model_val: str,
    n_rows: int,
) -> None:
    meta = {
        "mode": mode,
        "replicates_per_prompt": replicates,
        "prompt_keys": PROMPT_KEYS,
        "rubric_dimensions": RUBRIC_DIMENSIONS,
        "composite_definition": "equal-weighted mean of normalized positive dimensions (0-100)",
        "model_generator": model_gen,
        "model_validator": model_val,
        "n_rows": n_rows,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "run_metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def _validate_existing_reports(
    reports_dir: Path,
    base: str,
    api_key: str,
    model_val: str,
    scenario: str,
    output_dir: Path,
) -> Path:
    """Score reports that already exist on disk. Expects file naming `<prompt_key>_*.txt`.
    Files whose stem starts with a known prompt key are grouped accordingly; others get prompt_key='other'.
    """
    rows: list[dict] = []
    files = sorted([p for p in reports_dir.glob("*.txt") if p.is_file()])
    if not files:
        raise SystemExit(f"No .txt reports found in {reports_dir}")
    for path in files:
        stem = path.stem
        pk = next((k for k in PROMPT_KEYS if stem.startswith(k)), "other")
        rep_idx = sum(1 for r in rows if r["prompt_key"] == pk) + 1
        report_text = path.read_text(encoding="utf-8")
        print(f"\nValidate {path.name} (prompt_key={pk})")
        msgs = [
            {"role": "system", "content": "Output only valid JSON."},
            {"role": "user", "content": validator_prompt(report_text, scenario)},
        ]
        raw = ollama_chat(base, api_key, model_val, msgs, format_json=True, temperature=0.15)
        vj = extract_json_object(raw)
        scores = {d: vj.get(d, 0) for d in RUBRIC_DIMENSIONS}
        scores["overreach_severity"] = vj.get("overreach_severity", 0)
        rel = str(path.resolve())
        if pk == "other":
            PROMPT_LABELS.setdefault("other", "Other (external)")
            row = _row_from_scores("other", rep_idx, scores, vj.get("validator_notes", ""), rel)
        else:
            row = _row_from_scores(pk, rep_idx, scores, vj.get("validator_notes", ""), rel)
        rows.append(row)
        time.sleep(0.4)
    csv_path = output_dir / "validation_results.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    _write_metadata(
        output_dir,
        mode="external_reports",
        replicates=0,
        model_gen="(none)",
        model_val=model_val,
        n_rows=len(rows),
    )
    print(f"\nWrote {csv_path} ({len(rows)} rows)")
    return csv_path


def validate_one_report_file(
    report_path: Path,
    prompt_key: str | None,
    base: str,
    api_key: str,
    model_val: str,
    scenario: str,
    output_dir: Path,
) -> dict:
    """Score a single report via the live validator; write sidecar files (does not touch validation_results.csv)."""
    report_path = report_path.resolve()
    if not report_path.is_file():
        raise SystemExit(f"Not a file: {report_path}")

    stem = report_path.stem
    if stem.startswith("MOCK_"):
        stem = stem.removeprefix("MOCK_")
    pk = prompt_key
    if pk is None:
        pk = next((k for k in PROMPT_KEYS if stem.startswith(k)), None)
    if pk is None:
        raise SystemExit(
            f"Cannot infer prompt_key from filename '{report_path.name}'. "
            f"Pass --prompt-key with one of: {', '.join(PROMPT_KEYS)}"
        )

    report_text = report_path.read_text(encoding="utf-8")
    print(f"\n=== Validate ONE report: {report_path.name} (prompt_key={pk}) ===")
    print(f"Validator model: {model_val}")

    val_msgs = [
        {"role": "system", "content": "Output only valid JSON. No markdown fences."},
        {"role": "user", "content": validator_prompt(report_text, scenario)},
    ]
    raw_val = ollama_chat(base, api_key, model_val, val_msgs, format_json=True, temperature=0.15)
    try:
        vj = extract_json_object(raw_val)
    except Exception as e:
        print(f"VALIDATION PARSE ERROR: {e}\nRaw:\n{raw_val[:1200]}", file=sys.stderr)
        raise

    scores = {d: vj.get(d, 0) for d in RUBRIC_DIMENSIONS}
    scores["overreach_severity"] = vj.get("overreach_severity", 0)
    rel_path = str(report_path)
    row = _row_from_scores(pk, 1, scores, vj.get("validator_notes", ""), rel_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    one_csv = output_dir / "validation_one_report.csv"
    one_json = output_dir / "validation_one_report.json"
    pd.DataFrame([row]).to_csv(one_csv, index=False)

    bundle = {
        "report_path": rel_path,
        "prompt_key": pk,
        "validator_model": model_val,
        "row": row,
        "validator_scores_json": vj,
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    one_json.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n--- Scores ---")
    for d in RUBRIC_DIMENSIONS:
        print(f"  {d}: {row[d]}")
    print(f"  overreach_severity: {row['overreach_severity']}")
    print(f"  composite_score: {row['composite_score']}")
    print(f"  validator_notes: {row['validator_notes']}")
    print(f"\nWrote {one_csv}")
    print(f"Wrote {one_json}")
    print("(Full experiment CSV validation_results.csv was not modified.)")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Homework 3 validation experiment")
    parser.add_argument("--mock", action="store_true", help="No API; write synthetic CSV + stub reports")
    parser.add_argument("--replicates", type=int, default=None, help="Per prompt (default env or 6)")
    parser.add_argument(
        "--reports-dir",
        type=str,
        default=None,
        help="Validate existing .txt reports in this folder instead of generating new ones.",
    )
    parser.add_argument(
        "--validate-one",
        type=str,
        default=None,
        metavar="REPORT.txt",
        help=(
            "Live API: score a single .txt report only. Writes output/validation_one_report.csv "
            "+ .json; does not overwrite validation_results.csv."
        ),
    )
    parser.add_argument(
        "--prompt-key",
        type=str,
        default=None,
        choices=PROMPT_KEYS,
        help="With --validate-one, required only if the filename does not start with a known prompt key prefix.",
    )
    args = parser.parse_args()

    root = _hw3_root()
    load_dotenv(root / ".env")
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    replicates = args.replicates or int(os.environ.get("HW3_REPLICATES_PER_PROMPT", "6"))

    if args.mock:
        run_mock(output_dir, replicates)
        print("Next: python analyze_experiment.py")
        return

    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    base = os.environ.get("OLLAMA_HOST", "https://ollama.com").strip()
    model_gen = os.environ.get("OLLAMA_MODEL_GENERATOR", "").strip() or os.environ.get(
        "OLLAMA_MODEL", "nemotron-3-nano:30b-cloud"
    )
    model_val = os.environ.get("OLLAMA_MODEL_VALIDATOR", "").strip() or model_gen
    if not api_key:
        print("Set OLLAMA_API_KEY in .env at the project root (see .env.example). Or use --mock.", file=sys.stderr)
        sys.exit(1)
    scenario = _load_scenario()

    if args.validate_one:
        validate_one_report_file(
            Path(args.validate_one).resolve(),
            args.prompt_key,
            base,
            api_key,
            model_val,
            scenario,
            output_dir,
        )
        return

    if args.reports_dir:
        _validate_existing_reports(
            Path(args.reports_dir).resolve(), base, api_key, model_val, scenario, output_dir
        )
        print("Next: python analyze_experiment.py")
        return

    temps = {
        "A_minimal_paragraph": float(os.environ.get("HW3_GENERATION_TEMPERATURE_A", "0.85")),
        "B_structured_sections": float(os.environ.get("HW3_GENERATION_TEMPERATURE_B", "0.55")),
        "C_ICS_traceability": float(os.environ.get("HW3_GENERATION_TEMPERATURE_C", "0.35")),
    }

    rows: list[dict] = []
    for pk in PROMPT_KEYS:
        gen_messages = generation_messages(scenario, pk)
        for rep in range(1, replicates + 1):
            print(f"\n=== Generate {PROMPT_LABELS[pk]}  replicate {rep}/{replicates} ===")
            report_text = ollama_chat(
                base, api_key, model_gen, gen_messages, format_json=False, temperature=temps[pk]
            ).strip()
            rel = f"reports/{pk}_r{rep}.txt"
            p = output_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(report_text, encoding="utf-8")

            print(f"--- Validate ({model_val}) ---")
            val_msgs = [
                {"role": "system", "content": "Output only valid JSON. No markdown fences."},
                {"role": "user", "content": validator_prompt(report_text, scenario)},
            ]
            raw_val = ollama_chat(base, api_key, model_val, val_msgs, format_json=True, temperature=0.15)
            try:
                vj = extract_json_object(raw_val)
            except Exception as e:
                print(f"VALIDATION PARSE ERROR: {e}\nRaw:\n{raw_val[:800]}")
                raise
            scores = {d: vj.get(d, 0) for d in RUBRIC_DIMENSIONS}
            scores["overreach_severity"] = vj.get("overreach_severity", 0)
            rows.append(_row_from_scores(pk, rep, scores, vj.get("validator_notes", ""), rel))
            time.sleep(0.4)

    csv_path = output_dir / "validation_results.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    _write_metadata(
        output_dir, mode="live", replicates=replicates, model_gen=model_gen, model_val=model_val, n_rows=len(rows)
    )
    print(f"\nDone. Wrote {csv_path} ({len(rows)} rows). Next: python analyze_experiment.py")


if __name__ == "__main__":
    main()
