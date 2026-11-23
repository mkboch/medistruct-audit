# Codes/eval/eval_gold50_baselines.py
import json
import re
from pathlib import Path
from typing import List, Dict, Tuple
import pandas as pd
import sys

# add project root (the folder that contains "Codes/") to PYTHONPATH
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Codes.baselines.baseline_sections import baseline_from_sections
from Codes.baselines.baseline_scispacy import baseline_scispacy
from Codes.baselines.baseline_meds_section_v2 import baseline_meds_section_v2
from Codes.baselines.baseline_llm_notool import llm_extract_notool
from Codes.baselines.baseline_meds_strong import baseline_meds_strong


GOLD_CSV = Path("Outputs/gold50_pack/gold50_annotation.csv")
MAIN_DIR = Path("Outputs/discharge_extract_v2_run5k_FINAL/normalized")
OUT_CSV  = Path("Outputs/gold50_pack/gold50_compare_all.csv")


def split_cell(s) -> List[str]:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return []
    s = str(s).strip()
    if not s:
        return []
    if ";" in s:
        parts = [p.strip() for p in s.split(";")]
    else:
        parts = [p.strip() for p in s.splitlines()]
    return [p for p in parts if p]


def canon_strict(x: str) -> str:
    x = (x or "").strip().lower()
    x = re.sub(r"\s+", " ", x)
    return x


def canon_relaxed(x: str) -> str:
    x = (x or "").strip().lower()
    x = re.sub(r"\s+", " ", x)
    # remove dose/frequency-ish tokens to make relaxed comparable (generic)
    x = re.sub(r"\b\d+(\.\d+)?\b", "", x)
    x = re.sub(r"\b(mg|mcg|g|ml|units?|tablet|tab|capsule|po|iv|im|bid|tid|qid|qhs|prn|daily|q\d+h)\b", "", x)
    x = re.sub(r"[^\w\s\-/().%]", "", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x


# ---------- medication name-only canonicalization (v2) ----------
MED_STOPWORDS = set("""
po iv im sc pr qd daily bid tid qid qhs qam qpm qod
tablet tab tabs capsule cap caps patch inhaler neb nebulizer spray
mg mcg g gram grams ml l unit units meq %
w meals w/meals with meals
""".split())

def med_base_v2(x: str) -> str:
    """
    Normalize a medication string to a base drug name:
    - strip dose/frequency/routes
    - keep first 1-2 tokens as the base name (works well for many meds)
    """
    x = (x or "").lower().strip()
    x = re.sub(r"[^\w\s\-+/]", " ", x)
    x = re.sub(r"\s+", " ", x).strip()

    toks = []
    for t in x.split():
        if t in MED_STOPWORDS:
            continue
        if re.fullmatch(r"\d+(\.\d+)?", t):
            continue
        if re.fullmatch(r"\d+/\d+", t):
            continue
        toks.append(t)

    if not toks:
        return ""

    phrase = " ".join(toks)
    phrase = re.sub(r"\s+", " ", phrase).strip()
    parts = phrase.split()
    base = " ".join(parts[:2])
    return base.strip()


def set_scores(gold: List[str], pred: List[str], relaxed: bool, canon_fn=None) -> Tuple[float,float,float,float]:
    """
    Returns precision, recall, f1, jaccard on SETS after canonicalization.
    If canon_fn is given, it overrides strict/relaxed canonicalizers.
    """
    if canon_fn is None:
        c = canon_relaxed if relaxed else canon_strict
    else:
        c = canon_fn

    G = {c(x) for x in gold if c(x)}
    P = {c(x) for x in pred if c(x)}
    if not G and not P:
        return 1.0, 1.0, 1.0, 1.0

    inter = len(G & P)
    prec = inter / len(P) if P else 0.0
    rec  = inter / len(G) if G else 0.0
    f1   = (2*prec*rec/(prec+rec)) if (prec+rec) else 0.0
    jac  = inter / len(G | P) if (G | P) else 1.0
    return prec, rec, f1, jac


def load_note_text(row) -> str:
    p = row.get("full_text_path")
    if isinstance(p, str) and p and Path(p).exists():
        return Path(p).read_text(encoding="utf-8", errors="ignore")
    return str(row.get("note_text_preview") or "")


def load_main_struct(note_id: str) -> Dict:
    p = MAIN_DIR / f"{note_id}.json"
    if not p.exists():
        return {"symptoms":[], "diagnoses":[], "medications":[], "assessment_summary":""}
    return json.loads(p.read_text())


def main():
    df = pd.read_csv(GOLD_CSV)
    rows = []

    for _, row in df.iterrows():
        note_id = str(row["note_id"])
        note_text = load_note_text(row)

        # LLM no-tool baseline (same model/server)
        # Keep it light: same prompt template behavior as your extraction view is not required here,
        # since gold50 has a note_text_preview/full_text_path already.
        try:
            notool_obj, notool_raw = llm_extract_notool(
                model="Qwen/Qwen2.5-7B-Instruct",
                prompt=note_text,
                max_tokens=500,
                temperature=0.0,
                timeout_s=60,
                retries=2,
            )
        except Exception:
            notool_obj = {"symptoms": [], "diagnoses": [], "medications": [], "assessment_summary": ""}

        gold = {
            "symptoms": split_cell(row.get("gold_symptoms")),
            "diagnoses": split_cell(row.get("gold_diagnoses")),
            "medications": split_cell(row.get("gold_medications")),
        }

        ours = load_main_struct(note_id)
        baseA = baseline_from_sections(note_text)
        baseB = baseline_scispacy(note_text)
        baseC = baseline_meds_section_v2(note_text)
        baseM = baseline_meds_strong(note_text)

        
        for system_name, pred in [
            ("ours", ours),
            ("baseline_sections", baseA),
            ("baseline_scispacy", baseB),
            ("baseline_meds_section_v2", baseC),
            ("baseline_llm_notool", notool_obj),
            ("baseline_meds_strong", baseM),
        ]:
            out = {"note_id": note_id, "system": system_name}

            for field in ["symptoms","diagnoses","medications"]:
                g = gold[field]
                p = pred.get(field, []) or []

                if field == "medications":
                    # evaluate meds by base drug name (fair comparison)
                    ps, rs, f1s, js = set_scores(g, p, relaxed=False, canon_fn=med_base_v2)
                    pr, rr, f1r, jr = set_scores(g, p, relaxed=True,  canon_fn=med_base_v2)
                    out["medications_metric"] = "name_only_v2"
                else:
                    ps, rs, f1s, js = set_scores(g, p, relaxed=False)
                    pr, rr, f1r, jr = set_scores(g, p, relaxed=True)

                out[f"{field}_prec_strict"] = ps
                out[f"{field}_rec_strict"]  = rs
                out[f"{field}_f1_strict"]   = f1s
                out[f"{field}_jacc_strict"] = js

                out[f"{field}_prec_relaxed"] = pr
                out[f"{field}_rec_relaxed"]  = rr
                out[f"{field}_f1_relaxed"]   = f1r
                out[f"{field}_jacc_relaxed"] = jr

                out[f"{field}_gold_n"] = len(g)
                out[f"{field}_pred_n"] = len(p)

            rows.append(out)

    out_df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUT_CSV, index=False)

    cols = []
    for f in ["symptoms","diagnoses","medications"]:
        cols += [f"{f}_f1_relaxed", f"{f}_f1_strict"]

    print("Wrote:", OUT_CSV, "rows:", len(out_df))
    print("\nMEAN F1 by system:")
    print(out_df.groupby("system")[cols].mean().round(3))


if __name__ == "__main__":
    main()