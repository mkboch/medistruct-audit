"""CPU-only smoke tests for the historical extraction code (Part A), using synthetic text written from
scratch for this test (no clinical data). Run from the repository root:  python -m pytest -q tests
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODES = ROOT / "part_a_extraction" / "Codes"
sys.path.insert(0, str(CODES))
sys.path.insert(0, str(CODES / "baselines"))

SYNTHETIC_NOTE = """CHIEF COMPLAINT:
Synthetic cough

HISTORY OF PRESENT ILLNESS:
Test patient with synthetic cough and synthetic fever. Takes examplemab 10 mg daily at home.

HOSPITAL COURSE:
Treated for synthetic pneumonia.

DISCHARGE DIAGNOSES:
synthetic pneumonia

DISCHARGE MEDICATIONS:
1. examplemab 10 mg daily
2. testocillin 500 mg bid
"""


@pytest.fixture(scope="module")
def schema():
    return json.loads((CODES / "configs" / "schema_v2_spans.json").read_text())


def test_imports_and_prompts():
    import utils  # noqa: F401
    import run_extract  # noqa: F401
    for p in (CODES / "prompts").glob("*.txt"):
        t = p.read_text()
        assert "<<<" in t and ">>>" in t, p.name


def test_cli_parsing():
    for script in ("run_extract.py", "run_roundtrip_vllm.py", "roundtrip_eval.py", "mimic_discharge_loader.py"):
        r = subprocess.run([sys.executable, str(CODES / script), "--help"], capture_output=True, text=True, cwd=CODES)
        assert r.returncode == 0 and "usage" in r.stdout.lower(), (script, r.stderr[-500:])


def test_view_normalize_validate_evidence(schema):
    import utils
    from run_extract import generate_evidence_spans
    view, meta = utils.build_extraction_view(SYNTHETIC_NOTE, max_chars=26000)
    assert meta["strategy"] == "section_prioritized" and not meta["trimmed"]
    raw = {"symptoms": ["synthetic cough", "synthetic fever"], "diagnoses": ["synthetic pneumonia"],
           "medications": ["examplemab 10 mg daily", "testocillin 500 mg bid"], "assessment_summary": "Stable.",
           "evidence": []}
    norm = utils.normalize_structured(raw)
    norm["evidence"] = generate_evidence_spans(view, norm, cap_total=12, cap_meds=4)
    ok, errs = utils.validate_schema(norm, schema)
    assert ok, errs
    m = utils.evidence_span_checks(view, norm)
    assert m["span_valid_rate"] == 1.0 and m["substring_support_rate"] == 1.0
    assert m["evidence_count"] == 5 and m["coverage"] == 1.0
    # first-occurrence anchoring: the home-medication mention precedes the discharge-medication section
    assert m["med_section_alignment_rate"] == 0.5
    capped = generate_evidence_spans(view, norm, cap_total=2, cap_meds=4)
    assert len(capped) == 2


def test_failed_parse_is_schema_invalid(schema):
    import utils
    ok, _ = utils.validate_schema(utils.normalize_structured({}), schema)
    assert not ok


def test_jaccard_semantics():
    import utils
    assert utils.jaccard([], []) == 1.0
    assert utils.jaccard(["A "], ["a"]) == 1.0
    assert utils.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)


def test_roundtrip_eval_cli(tmp_path):
    s, sp = tmp_path / "S", tmp_path / "Sp"
    s.mkdir(), sp.mkdir()
    (s / "n1.json").write_text(json.dumps({"symptoms": ["a", "b"], "diagnoses": ["x"], "medications": []}))
    (sp / "n1.json").write_text(json.dumps({"symptoms": ["b", "c"], "diagnoses": ["x"], "medications": []}))
    out = tmp_path / "rt.csv"
    subprocess.run([sys.executable, str(CODES / "roundtrip_eval.py"), "--s_dir", str(s), "--sp_dir", str(sp),
                    "--out_csv", str(out)], check=True, cwd=CODES, capture_output=True)
    row = next(csv.DictReader(open(out)))
    assert float(row["symptoms_jaccard"]) == pytest.approx(1 / 3)
    assert float(row["diagnoses_jaccard"]) == 1.0 and float(row["medications_jaccard"]) == 1.0


def test_section_baseline_and_gold50_reconstruction(tmp_path):
    from baseline_sections import baseline_from_sections
    b = baseline_from_sections(SYNTHETIC_NOTE)
    assert "examplemab 10 mg daily" in b["medications"] and "synthetic pneumonia" in b["diagnoses"]
    pred = tmp_path / "pred"
    pred.mkdir()
    (pred / "n1.json").write_text(json.dumps({"symptoms": ["Synthetic cough"], "diagnoses": ["synthetic pneumonia", "extra dx"],
                                              "medications": ["Examplemab 20 MG BID"]}))
    gold = tmp_path / "gold.csv"
    with open(gold, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["note_id", "gold_symptoms", "gold_diagnoses", "gold_medications"])
        w.writerow(["n1", "synthetic cough; synthetic fever", "synthetic pneumonia", "examplemab 10 mg daily; testocillin"])
    notes = tmp_path / "notes.jsonl"
    notes.write_text(json.dumps({"note_id": "n1", "text": SYNTHETIC_NOTE}) + "\n")
    sys.path.insert(0, str(ROOT / "part_a_extraction" / "reconstructed"))
    import gold50_micro_summary as g
    out = g.main(["--gold_csv", str(gold), "--pred_dir", str(pred), "--notes_jsonl", str(notes)])
    assert out["ours"]["symptoms"]["tp"] == 1 and out["ours"]["symptoms"]["n_gold"] == 2
    assert out["ours"]["diagnoses"]["micro_precision"] == 0.5
    assert out["ours"]["medications"]["tp"] == 1          # dose/frequency ignored for medications
    assert "baseline_sections" in out
