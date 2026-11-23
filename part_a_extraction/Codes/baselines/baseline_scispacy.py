# Codes/baselines/baseline_scispacy.py
from typing import Dict, List
import re

import spacy

# small, fast
NLP = spacy.load("en_core_sci_sm")

def _uniq_keep_order(xs: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in xs:
        k = x.lower().strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out

def baseline_scispacy(note_text: str) -> Dict[str, object]:
    text = note_text or ""
    # avoid very long docs; take first N chars (baseline)
    text = text[:20000]

    doc = NLP(text)

    # en_core_sci_sm is mostly generic scientific NER.
    # We'll treat entities as "diagnoses/symptoms" and leave meds empty unless you add a drug linker.
    ents = [e.text.strip() for e in doc.ents if e.text and len(e.text.strip()) >= 2]

    # crude split: anything that looks like dosing goes to meds candidate
    meds = []
    dxsym = []
    dose_re = re.compile(r"\b(mg|mcg|g|units?|tablet|tab|capsule|po|iv|bid|tid|qhs|prn)\b", re.I)
    for e in ents:
        if dose_re.search(e):
            meds.append(e)
        else:
            dxsym.append(e)

    dxsym = _uniq_keep_order(dxsym)[:30]
    meds = _uniq_keep_order(meds)[:30]

    # split dx vs symptoms is hard without UMLS linker; keep as diagnoses baseline and empty symptoms baseline
    return {
        "symptoms": [],
        "diagnoses": dxsym,
        "medications": meds,
        "assessment_summary": "",
    }