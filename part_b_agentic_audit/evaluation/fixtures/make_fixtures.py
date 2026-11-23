"""Generate synthetic audit cases with controlled, known faults.

Each case directory mimics the layout of the historical extraction run (receipts, normalized,
views, source notes, run metadata, extractor code, prompt, reference labels, roundtrip scores,
reported claims). Note text is template-generated from a small vocabulary and contains no real
patient data.

Reference answers are derived from the generation knobs (the injected faults), independently of
the audit tools, and are written to a separate directory that the agent sandbox does not mount.

Usage: python make_fixtures.py --out <cases_root> --refs <refs_dir> --registry <cases.json> --spec <spec.json>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from pathlib import Path

SYM = ["chest pain", "shortness of breath", "fever", "productive cough", "nausea", "fatigue", "dizziness",
       "abdominal pain", "headache", "leg swelling", "palpitations", "dysuria"]
SYM_PARA = {"shortness of breath": "difficulty breathing", "leg swelling": "lower extremity edema",
            "fatigue": "generalized weakness", "dizziness": "lightheadedness", "fever": "febrile illness"}
DX = ["community acquired pneumonia", "acute on chronic heart failure", "atrial fibrillation", "acute kidney injury",
      "urinary tract infection", "cellulitis", "copd exacerbation", "hyperkalemia", "sepsis", "iron deficiency anemia",
      "type 2 diabetes mellitus", "hypertension"]
MED = ["aspirin 81 mg daily", "metoprolol succinate 25 mg daily", "furosemide 40 mg daily", "lisinopril 10 mg daily",
       "atorvastatin 40 mg qhs", "apixaban 5 mg bid", "cefpodoxime 200 mg bid", "insulin glargine 20 units qhs",
       "omeprazole 20 mg daily", "prednisone 40 mg daily", "tamsulosin 0.4 mg qhs", "potassium chloride 20 meq daily"]

POSTHOC_CODE = '''\
def attach_evidence(view_text, structured, cap_total={cap_total}, cap_meds={cap_meds}):
    low = view_text.lower()
    evidence = []
    for field in ["symptoms", "diagnoses", "medications"]:
        for item in structured.get(field, []):
            if cap_total and len(evidence) >= cap_total:
                break
            needle = item.strip().lower()
            idx = low.find(needle)
            if idx == -1:
                continue
            evidence.append({{"field": field, "item": item, "start": idx, "end": idx + len(needle)}})
    return evidence

def postprocess(view_text, parsed):
    norm = normalize(parsed)
    norm["evidence"] = attach_evidence(view_text, norm)
    return norm
'''
MODEL_CODE = '''\
def postprocess(source_text, parsed):
    norm = normalize(parsed)
    # evidence spans are returned by the model; keep only well-formed objects
    norm_ev = []
    for e in parsed.get("evidence", []):
        if all(k in e for k in ("field", "item", "start", "end")):
            norm_ev.append(e)
    norm.update(evidence=norm_ev)
    return norm
'''
PROMPT_POSTHOC = 'Extract symptoms, diagnoses, medications and an assessment_summary as JSON.\nEVIDENCE: set "evidence": [] (empty list). Do not output evidence.\nNOTE_TEXT:\n<<<NOTE_TEXT>>>\n'
PROMPT_MODEL = ('Extract symptoms, diagnoses, medications and an assessment_summary as JSON.\n'
                'For every extracted item, add an evidence object with field, item, and the start and end '
                'character offsets of the supporting text in NOTE_TEXT.\nNOTE_TEXT:\n<<<NOTE_TEXT>>>\n')
HEADERS = ["CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "PAST MEDICAL HISTORY", "PHYSICAL EXAM",
           "HOSPITAL COURSE", "DISCHARGE MEDICATIONS", "DISCHARGE DIAGNOSES"]
VIEW_ORDER = ["CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "HOSPITAL COURSE", "DISCHARGE DIAGNOSES",
              "DISCHARGE MEDICATIONS"]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def make_note(rng: random.Random, has_med_section: bool):
    sy = rng.sample(SYM, rng.randint(2, 5))
    dx = rng.sample(DX, rng.randint(2, 5))
    md = rng.sample(MED, rng.randint(4, 9))
    hpi_meds = rng.sample(md, min(len(md), rng.randint(1, 3)))  # meds also mentioned earlier in text
    sec = {
        "CHIEF COMPLAINT": sy[0],
        "HISTORY OF PRESENT ILLNESS": f"Patient presented with {', '.join(sy)}. Home medications include {', '.join(hpi_meds)}.",
        "PAST MEDICAL HISTORY": ", ".join(rng.sample(DX, 2)),
        "PHYSICAL EXAM": "Vitals stable. Lungs with scattered crackles. No focal deficits.",
        "HOSPITAL COURSE": f"Treated for {', '.join(dx)}. Received {', '.join(rng.sample(md, min(2, len(md))))} in hospital.",
        "DISCHARGE MEDICATIONS": "\n".join(f"{i + 1}. {m}" for i, m in enumerate(md)),
        "DISCHARGE DIAGNOSES": "\n".join(dx),
    }
    if not has_med_section:
        sec.pop("DISCHARGE MEDICATIONS")
    return sy, dx, md, sec


def render(sec: dict, order: list) -> str:
    return "\n\n".join(f"{h}:\n{sec[h]}" for h in order if h in sec)


def sections_span(text: str) -> dict:
    up = text.upper()
    heads = ["CHIEF COMPLAINT", "HISTORY OF PRESENT ILLNESS", "PAST MEDICAL HISTORY", "HOSPITAL COURSE",
             "DISCHARGE DIAGNOSES", "DISCHARGE MEDICATIONS", "PHYSICAL EXAM"]
    hits = sorted((up.find(h), h) for h in heads if up.find(h) != -1)
    return {h: (i, hits[k + 1][0] if k + 1 < len(hits) else len(text)) for k, (i, h) in enumerate(hits)}


def toks(x):
    return {t for t in re.sub(r"[^\w\s]", " ", x.lower()).split() if len(t) >= 3}


def gen_metrics(text: str, S: dict, med_impl: str):
    L = len(text)
    ev = S["evidence"]
    valid = [0 <= e["start"] < e["end"] <= L for e in ev]
    sup = {(e["field"], e["item"].strip().lower()) for e, v in zip(ev, valid) if v}
    items = [(f, x.strip().lower()) for f in ("symptoms", "diagnoses", "medications") for x in S[f]]
    cov = sum(k in sup for k in items) / len(items) if items else 0.0
    sflags = [1 if toks(text[e["start"]:e["end"]]) & toks(e["item"]) else 0 for e, v in zip(ev, valid) if v and toks(e["item"])]
    secs = sections_span(text)
    med = secs.get("DISCHARGE MEDICATIONS")
    mev = [e for e in ev if e["field"] == "medications"]
    if med and mev:
        align = sum(med[0] <= e["start"] and e["end"] <= med[1] for e in mev) / len(mev)
    else:
        align = 0.0 if med_impl == "zero_fill" else None
    present = {f: (sum((f, x.strip().lower()) in sup for x in S[f]) / len(S[f]) if S[f] else 0.0)
               for f in ("symptoms", "diagnoses", "medications")}
    return {"evidence_count": len(ev), "span_valid_rate": sum(valid) / len(valid) if valid else 0.0,
            "coverage": cov, "substring_support_rate": sum(sflags) / len(sflags) if sflags else 0.0,
            "med_section_alignment_rate": align, "evidence_present_rate": present}, (med is not None and bool(mev))


def med_base(x):
    stop = set("po iv im sc pr qd daily bid tid qid qhs qam qpm qod tablet tab tabs capsule cap caps patch inhaler neb "
               "nebulizer spray mg mcg g gram grams ml l unit units meq % w meals w/meals with".split())
    x = re.sub(r"[^\w\s\-+/]", " ", x.lower())
    t = [w for w in x.split() if w not in stop and not re.fullmatch(r"\d+(\.\d+)?", w) and not re.fullmatch(r"\d+/\d+", w)]
    return " ".join(t[:2])


def micro_f1(pairs, field):
    c = med_base if field == "medications" else (lambda s: re.sub(r"\s+", " ", s.strip().lower()))
    TP = P = G = 0
    for gold, pred in pairs:
        Gs, Ps = {c(x) for x in gold if c(x)}, {c(x) for x in pred if c(x)}
        TP, P, G = TP + len(Gs & Ps), P + len(Ps), G + len(Gs)
    p, r = (TP / P if P else 0), (TP / G if G else 0)
    return 2 * p * r / (p + r) if p + r else 0.0


def build_case(spec: dict, out_root: Path, refs: Path) -> dict:
    rng = random.Random(spec["seed"])
    cid = spec["case_id"]
    d = out_root / cid
    for sub in ("receipts", "normalized", "views", "code"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    posthoc = spec["evidence_mode"] == "posthoc"
    cap_total, cap_meds = spec.get("cap_total"), spec.get("cap_meds")
    view_reordered = spec.get("view_reordered", posthoc)
    prompt = PROMPT_POSTHOC if posthoc else PROMPT_MODEL
    (d / "prompt.txt").write_text(prompt)
    (d / "code" / "run_extract.py").write_text(
        POSTHOC_CODE.format(cap_total=cap_total or 0, cap_meds=cap_meds or 0) if posthoc else MODEL_CODE)
    psha, ssha = sha(prompt), sha("schema-v2")
    n, nfail = spec["n_notes"], spec.get("n_fail", 0)
    fail_subject_clustered = spec.get("fail_clustered", False)
    hetero_frac = spec.get("hetero_frac", 0.0)
    src_lines, recs = [], []
    stats = {"n_cap_hit": 0, "na_zero": 0, "appl": [], "valid_align": []}
    gold_pairs = {f: [] for f in ("symptoms", "diagnoses", "medications")}
    gold_rows = []
    nid_list = [f"syn-{cid}-{i:04d}" for i in range(n)]
    fail_ids = set(rng.sample(nid_list, nfail)) if nfail else set()
    hetero_ids = set(rng.sample([x for x in nid_list if x not in fail_ids], int(hetero_frac * n)))
    for i, nid in enumerate(nid_list):
        subj = 9000 + (0 if (nid in fail_ids and fail_subject_clustered) else rng.randint(1, 400) * 10 + i)
        has_med = rng.random() >= spec.get("no_med_section_frac", 0.0)
        sy, dx, md, sec = make_note(rng, has_med)
        source = render(sec, HEADERS)
        view = render(sec, VIEW_ORDER) if view_reordered else source
        src_lines.append(json.dumps({"note_id": nid, "subject_id": subj, "text": source}))
        (d / "views" / f"{nid}.txt").write_text(view)
        mt, vc = (400, 16000) if nid in hetero_ids else (500, 26000)
        rec = {"note_id": nid, "subject_id": subj, "model": spec.get("model", "local-llm-7b"),
               "prompt_sha256": psha, "schema_sha256": ssha,
               "decoding": {"temperature": 0.0, "top_p": 1.0, "max_tokens": mt},
               "input_preprocessing": {"extraction_view": {"max_chars": vc, "view_chars": len(view), "trimmed": False}}}
        if nid in fail_ids:
            err = rng.choice(["Unterminated string starting at: line 40 column 9",
                              "HTTP 400: You passed 9000 input tokens; maximum context length is 8192"])
            rec.update(parse={"json_ok": False, "error": err}, schema_validation={"is_valid": False, "errors": []},
                       evidence_metrics={"evidence_count": 0, "span_valid_rate": 0.0, "coverage": 0.0,
                                         "substring_support_rate": 0.0, "med_section_alignment_rate": 0.0,
                                         "evidence_present_rate": {"symptoms": 0.0, "diagnoses": 0.0, "medications": 0.0}})
            (d / "normalized" / f"{nid}.json").write_text(json.dumps({"symptoms": [], "diagnoses": [], "medications": [],
                                                                      "assessment_summary": "", "evidence": []}))
            recs.append(rec)
            continue
        p_para = spec.get("p_paraphrase", 0.15)
        S = {"symptoms": [SYM_PARA[s] if s in SYM_PARA and rng.random() < p_para * 2 else s for s in sy],
             "diagnoses": [x for x in dx], "medications": list(md), "assessment_summary": "Stable for discharge."}
        if rng.random() < 0.5:
            S["diagnoses"].append(rng.choice([x for x in DX if x not in dx]))  # over-extraction
        if posthoc:
            low = view.lower()
            ev, nm = [], 0
            for f in ("symptoms", "diagnoses", "medications"):
                for it in S[f]:
                    if cap_total and len(ev) >= cap_total:
                        break
                    if f == "medications" and cap_meds and nm >= cap_meds:
                        break
                    k = low.find(it.lower())
                    if k == -1:
                        continue
                    ev.append({"field": f, "item": it, "start": k, "end": k + len(it)})
                    nm += f == "medications"
            n_found = sum(it.lower() in low for f in ("symptoms", "diagnoses", "medications") for it in S[f])
            stats["n_cap_hit"] += bool(cap_total) and n_found > cap_total
            metric_text = view
        else:
            low = source.lower()
            secs = sections_span(source)
            ev = []
            for f in ("symptoms", "diagnoses", "medications"):
                for it in S[f]:
                    occ = [m.start() for m in re.finditer(re.escape(it.lower()), low)]
                    if not occ:
                        continue
                    k = occ[-1]  # model cites the discharge-section mention (last occurrence)
                    if f == "medications" and "DISCHARGE MEDICATIONS" in secs:
                        a, b = secs["DISCHARGE MEDICATIONS"]
                        inside = [o for o in occ if a <= o < b]
                        k = inside[0] if inside else k
                    s, e = k, k + len(it)
                    if rng.random() < spec.get("p_bad_offset", 0.03):
                        s, e = s + 3, e + 3  # model offset error
                    ev.append({"field": f, "item": it, "start": s, "end": e})
            metric_text = source
        S["evidence"] = ev
        m, applicable = gen_metrics(metric_text, S, spec.get("med_impl", "zero_fill"))
        if not applicable and m["med_section_alignment_rate"] == 0.0:
            stats["na_zero"] += 1
        if applicable:
            stats["appl"].append(m["med_section_alignment_rate"])
        if m["med_section_alignment_rate"] is not None:
            stats["valid_align"].append(m["med_section_alignment_rate"])
        rec.update(parse={"json_ok": True, "error": ""}, schema_validation={"is_valid": True, "errors": []},
                   evidence_metrics=m)
        (d / "normalized" / f"{nid}.json").write_text(json.dumps(S))
        recs.append(rec)
        if len(gold_rows) < spec.get("n_gold", 20):
            gold = {"symptoms": sy, "diagnoses": dx, "medications": md}
            gold_rows.append({"note_id": nid, **{f"gold_{f}": "; ".join(v) for f, v in gold.items()}})
            for f in gold_pairs:
                gold_pairs[f].append((gold[f], S[f]))
    for r in recs:
        (d / "receipts" / f"{r['note_id']}.json").write_text(json.dumps(r))
    malformed = rng.sample([r["note_id"] for r in recs], spec.get("n_malformed", 0))
    for nid in malformed:
        (d / "receipts" / f"{nid}.json").write_text('{"note_id": "' + nid + '", "parse": {"json_ok": tr')
    (d / "source.jsonl").write_text("\n".join(src_lines) + "\n")
    # run metadata
    metas = [{"model": spec.get("model", "local-llm-7b"), "prompt_sha256": psha, "schema_sha256": ssha,
              "decoding": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 500}, "max_view_chars": 26000,
              "retry_max_view_chars": 26000, "inputs": "source.jsonl", "run_started_utc": "2026-01-01T00:00:00Z"}]
    if hetero_ids and spec.get("hetero_documented", True):
        metas.append({**metas[0], "decoding": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 400},
                      "max_view_chars": 16000, "retry_max_view_chars": 16000})
    meta_files = []
    for k, m in enumerate(metas):
        p = d / f"run_meta_{k}.json"
        if spec.get("drop_run_meta") and k == 0:
            meta_files.append(p.name)  # registered but missing
            continue
        p.write_text(json.dumps(m))
        meta_files.append(p.name)
    # gold + roundtrip
    if not spec.get("drop_gold"):
        with open(d / "gold.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["note_id", "gold_symptoms", "gold_diagnoses", "gold_medications"])
            w.writeheader()
            w.writerows(gold_rows)
    rt_rows = [{"note_id": r["note_id"], **{f"{f}_jaccard": round(rng.uniform(0.4, 1.0), 4) for f in
                                            ("symptoms", "diagnoses", "medications")}} for r in gold_rows]
    if not spec.get("drop_roundtrip"):
        with open(d / "roundtrip.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rt_rows[0]))
            w.writeheader()
            w.writerows(rt_rows)
    # true values for claims (generator's own computations)
    parsed = [r for r in recs if r["note_id"] not in malformed]
    valid = [r for r in parsed if r["parse"]["json_ok"]]

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    true = {"schema_valid_count": len(valid)}
    for k in ("span_valid_rate", "coverage", "substring_support_rate", "med_section_alignment_rate", "evidence_count"):
        true[f"mean_{k}|valid"] = mean([r["evidence_metrics"][k] for r in valid])
        true[f"mean_{k}|all"] = mean([(r["evidence_metrics"][k] or 0.0) for r in parsed])
    for f in gold_pairs:
        true[f"gold_micro_f1_{f}"] = micro_f1(gold_pairs[f], f)
        true[f"roundtrip_jaccard_{f}"] = mean([r[f"{f}_jaccard"] for r in rt_rows])
    # claims
    claims, ref = [], {"case_id": cid, "issues": [], "notes": []}
    perturb = set(spec.get("perturb_claims", []))
    all_conv = set(spec.get("all_records_convention", []))
    keys = ["schema_valid_count", "mean_span_valid_rate", "mean_coverage", "mean_substring_support_rate",
            "mean_med_section_alignment_rate", "mean_evidence_count", "gold_micro_f1_symptoms",
            "gold_micro_f1_diagnoses", "gold_micro_f1_medications", "roundtrip_jaccard_symptoms",
            "roundtrip_jaccard_diagnoses", "roundtrip_jaccard_medications"]
    for j, key in enumerate(keys):
        cid_ = f"C{j + 1:02d}"
        if key.startswith("mean_"):
            v = true[f"{key}|{'all' if key in all_conv else 'valid'}"]
        else:
            v = true[key]
        if v is None:
            continue
        if key in perturb:
            v = v + (3 if key == "schema_valid_count" else rng.choice([-1, 1]) * rng.uniform(0.02, 0.06))
            ref["issues"].append({"code": "CLAIM_NOT_REPRODUCED", "target": cid_})
        rv = int(v) if key == "schema_valid_count" else round(v, 4)
        claims.append({"claim_id": cid_, "text": f"Reported {key.replace('_', ' ')}", "metric_key": key,
                       "reported_value": rv, "tolerance": 0 if key == "schema_valid_count" else 0.001})
    (d / "claims.json").write_text(json.dumps({"source": "synthetic manuscript table", "claims": claims}, indent=1))
    # ---- reference issues from knobs
    I = ref["issues"]
    mean_keys = [k for k in all_conv if true.get(f"{k}|all") is not None]
    if mean_keys and nfail and any(k not in all_conv for k in keys if k.startswith("mean_")):
        diff = [k for k in mean_keys if abs(true[f"{k}|all"] - true[f"{k}|valid"]) > 0.001]
        if diff:
            I.append({"code": "MIXED_DENOMINATORS", "target": ""})
    if posthoc:
        I.append({"code": "EVIDENCE_POSTHOC", "target": ""})
        I += [{"code": "TAUTOLOGICAL_METRIC", "target": "span_valid_rate"},
              {"code": "TAUTOLOGICAL_METRIC", "target": "substring_support_rate"}]
        if cap_total and stats["n_cap_hit"] >= 0.2 * len(valid):
            I.append({"code": "EVIDENCE_CAP_LIMITS_COVERAGE", "target": ""})
        if view_reordered:
            I.append({"code": "OFFSETS_NOT_IN_SOURCE", "target": ""})
    appl_mean = mean(stats["appl"])
    valid_mean = mean(stats["valid_align"])
    if stats["na_zero"] > 0 and (appl_mean is None or valid_mean is None or abs(appl_mean - valid_mean) >= 0.01):
        I.append({"code": "ZERO_FILLED_DENOMINATOR", "target": "med_section_alignment_rate"})
    if hetero_ids:
        I.append({"code": "HETEROGENEOUS_RUN_SETTINGS", "target": ""})
    if (hetero_ids and not spec.get("hetero_documented", True)) or spec.get("drop_run_meta"):
        I.append({"code": "MISSING_RUN_METADATA", "target": ""})
    if malformed:
        I.append({"code": "MALFORMED_RECEIPTS", "target": ""})
    if spec.get("drop_gold"):
        I.append({"code": "MISSING_ARTIFACT", "target": "gold_csv"})
    if spec.get("drop_roundtrip"):
        I.append({"code": "MISSING_ARTIFACT", "target": "roundtrip_csv"})
    if nfail >= 2 and fail_subject_clustered:
        I.append({"code": "FAILURES_CLUSTERED", "target": ""})
    ref["unverifiable_claims"] = [c["claim_id"] for c in claims
                                  if (spec.get("drop_gold") and c["metric_key"].startswith("gold_"))
                                  or (spec.get("drop_roundtrip") and c["metric_key"].startswith("roundtrip_"))]
    I[:] = [i for i in I if not (i["code"] == "CLAIM_NOT_REPRODUCED" and i["target"] in ref["unverifiable_claims"])]
    ref["knobs"] = spec
    refs.mkdir(parents=True, exist_ok=True)
    (refs / f"{cid}.json").write_text(json.dumps(ref, indent=1))
    reg = {"base": str(d), "description": "Saved outputs of a structured-extraction run (receipts, normalized outputs, views, source notes, metadata, code, prompt, labels, roundtrip, reported claims).",
           "receipts_dir": "receipts", "normalized_dir": "normalized", "views_dir": "views", "source_jsonl": "source.jsonl",
           "run_meta_files": meta_files, "extractor_code": ["code/run_extract.py"], "prompt_file": "prompt.txt",
           "gold_csv": "gold.csv", "roundtrip_csv": "roundtrip.csv", "claims_file": "claims.json"}
    return reg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--spec", required=True)
    a = ap.parse_args()
    specs = json.loads(Path(a.spec).read_text())
    reg_path = Path(a.registry)
    reg = json.loads(reg_path.read_text()) if reg_path.exists() else {}
    for s in specs:
        reg[s["case_id"]] = build_case(s, Path(a.out), Path(a.refs))
        print("built", s["case_id"])
    reg_path.write_text(json.dumps(reg, indent=1))


if __name__ == "__main__":
    main()
