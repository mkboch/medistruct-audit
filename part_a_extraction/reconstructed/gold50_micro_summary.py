"""RECONSTRUCTED (2026-11-23): micro-averaged precision/recall/F1 for the gold50 reference set.

The original script that wrote `gold50_summary_table.csv` was not preserved (the file
`Codes/eval/eval_gold50_ours_v2.py` is empty). This reconstruction reuses the matching rules of the
surviving `Codes/eval/eval_gold50_baselines.py` (lower-case + whitespace-collapsed exact set match for
symptoms and diagnoses; medications compared on base drug name = first two tokens after removing
dose/unit/route/frequency/form tokens) and pools TP/P/G counts over notes before computing F1.

Validated against the restricted historical artifacts on the original server (not distributed):
  ours:              symptoms 0.3275, diagnoses 0.2618, medications 0.5064
  baseline_sections: symptoms 0.0132, diagnoses 0.0502, medications 0.1697
The original summary table reported 0.326 / 0.261 / 0.504 (ours) and 0.157 (baseline medications);
those values could not be reproduced exactly and the reconstruction is NOT claimed to be identical
to the lost original.

Inputs (all must come from your own authorised data; nothing is distributed):
  --gold_csv   CSV with columns note_id, gold_symptoms, gold_diagnoses, gold_medications
               (items separated by ';' or newlines)
  --pred_dir   directory of <note_id>.json files with lists 'symptoms', 'diagnoses', 'medications'
  --notes_jsonl (optional) JSONL with note_id and text; enables the section-parser baseline
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

FIELDS = ("symptoms", "diagnoses", "medications")
MED_STOPWORDS = set("""
po iv im sc pr qd daily bid tid qid qhs qam qpm qod
tablet tab tabs capsule cap caps patch inhaler neb nebulizer spray
mg mcg g gram grams ml l unit units meq %
w meals w/meals with meals
""".split())
csv.field_size_limit(10**9)


def split_cell(s) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    parts = s.split(";") if ";" in s else s.splitlines()
    return [p.strip() for p in parts if p.strip()]


def canon_strict(x: str) -> str:
    return re.sub(r"\s+", " ", (x or "").strip().lower())


def med_base_v2(x: str) -> str:
    x = (x or "").lower().strip()
    x = re.sub(r"[^\w\s\-+/]", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    toks = [t for t in x.split() if t not in MED_STOPWORDS and not re.fullmatch(r"\d+(\.\d+)?", t)
            and not re.fullmatch(r"\d+/\d+", t)]
    return " ".join(" ".join(toks).split()[:2]).strip() if toks else ""


def micro(pairs, field):
    c = med_base_v2 if field == "medications" else canon_strict
    tp = n_pred = n_gold = 0
    for gold, pred in pairs:
        G = {c(x) for x in gold if c(x)}
        P = {c(str(x)) for x in pred if c(str(x))}
        tp, n_pred, n_gold = tp + len(G & P), n_pred + len(P), n_gold + len(G)
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gold if n_gold else 0.0
    return {"tp": tp, "n_pred": n_pred, "n_gold": n_gold, "micro_precision": p, "micro_recall": r,
            "micro_f1": 2 * p * r / (p + r) if p + r else 0.0}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold_csv", required=True)
    ap.add_argument("--pred_dir", required=True)
    ap.add_argument("--notes_jsonl", default=None)
    ap.add_argument("--out_json", default=None)
    a = ap.parse_args(argv)
    rows = list(csv.DictReader(open(a.gold_csv, encoding="utf-8", errors="replace")))
    preds = {}
    for r in rows:
        p = Path(a.pred_dir) / f"{r['note_id']}.json"
        preds[r["note_id"]] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    out = {"n_notes": len(rows), "ours": {f: micro([(split_cell(r[f"gold_{f}"]), preds[r["note_id"]].get(f) or [])
                                                     for r in rows], f) for f in FIELDS}}
    if a.notes_jsonl:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Codes" / "baselines"))
        from baseline_sections import baseline_from_sections
        texts = {}
        with open(a.notes_jsonl, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                texts[str(rec["note_id"])] = rec.get("text", "")
        base = {r["note_id"]: baseline_from_sections(texts.get(r["note_id"], "")) for r in rows}
        out["baseline_sections"] = {f: micro([(split_cell(r[f"gold_{f}"]), base[r["note_id"]].get(f) or [])
                                              for r in rows], f) for f in FIELDS}
    js = json.dumps(out, indent=2)
    if a.out_json:
        Path(a.out_json).write_text(js)
    print(js)
    return out


if __name__ == "__main__":
    main()
