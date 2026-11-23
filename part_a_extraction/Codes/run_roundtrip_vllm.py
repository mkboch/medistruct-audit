import argparse
import json
import os
from pathlib import Path
from typing import Dict, Any

from openai import OpenAI

from utils import (
    utc_now_iso, sha256_text, sha256_file,
    safe_json_loads, normalize_structured, validate_schema, jaccard,
    normalize_for_containment
)

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def regen_prompt(template: str, structured_json: str) -> str:
    return template.replace("<<<STRUCTURED_JSON>>>", structured_json)

def extract_prompt(template: str, note_text: str) -> str:
    return template.replace("<<<NOTE_TEXT>>>", note_text)

def call_llm_text(client: OpenAI, model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        top_p=1.0,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": "You are a careful clinical writer."},
            {"role": "user", "content": prompt}
        ],
    )
    return resp.choices[0].message.content

def call_llm_json(client: OpenAI, model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        top_p=1.0,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": "You are a careful clinical information extraction assistant."},
            {"role": "user", "content": prompt}
        ],
    )
    return resp.choices[0].message.content

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True, help="JSONL inputs")
    ap.add_argument("--extract_run_dir", required=True, help="Existing extraction run dir containing normalized/*.json")
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--extract_prompt", default="Codes/prompts/extract_v1.txt")
    ap.add_argument("--regen_prompt", default="Codes/prompts/regen_v1.txt")
    ap.add_argument("--model", required=True)
    ap.add_argument("--base_url", default=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"))
    ap.add_argument("--api_key", default=os.environ.get("OPENAI_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--regen_max_tokens", type=int, default=1400)
    ap.add_argument("--reextract_max_tokens", type=int, default=1100)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "regen").mkdir(exist_ok=True)
    (out_dir / "reextract_raw").mkdir(exist_ok=True)
    (out_dir / "reextract_norm").mkdir(exist_ok=True)

    schema_path = Path(args.schema)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_hash = sha256_file(schema_path)

    extract_template = load_text(Path(args.extract_prompt))
    regen_template = load_text(Path(args.regen_prompt))

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    extract_norm_dir = Path(args.extract_run_dir) / "normalized"
    if not extract_norm_dir.exists():
        raise SystemExit(f"Missing normalized dir: {extract_norm_dir}")

    rows = ["note_id,symptoms_jaccard,diagnoses_jaccard,medications_jaccard,reextract_schema_valid"]
    n = 0

    with Path(args.inputs).open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            note_id = str(rec["note_id"])

            s_path = extract_norm_dir / f"{note_id}.json"
            if not s_path.exists():
                continue
            S = json.loads(s_path.read_text(encoding="utf-8"))
            S_str = json.dumps(S, ensure_ascii=False)

            # Regenerate note Û
            rp = regen_prompt(regen_template, S_str)
            regen_text = call_llm_text(client, args.model, rp, args.regen_max_tokens, args.temperature)
            regen_path = out_dir / "regen" / f"{note_id}.txt"
            regen_path.write_text(regen_text, encoding="utf-8")

            # Re-extract S'
            ep = extract_prompt(extract_template, regen_text)
            re_raw = call_llm_json(client, args.model, ep, args.reextract_max_tokens, args.temperature)
            re_raw_path = out_dir / "reextract_raw" / f"{note_id}.txt"
            re_raw_path.write_text(re_raw, encoding="utf-8")

            ok, parsed, err = safe_json_loads(re_raw)
            Sprime = normalize_structured(parsed) if ok and isinstance(parsed, dict) else {}
            valid, _errs = validate_schema(Sprime, schema)
            re_norm_path = out_dir / "reextract_norm" / f"{note_id}.json"
            re_norm_path.write_text(json.dumps(Sprime, indent=2, ensure_ascii=False), encoding="utf-8")

            sym_j = jaccard(S.get("symptoms", []), Sprime.get("symptoms", []))
            dx_j  = jaccard(S.get("diagnoses", []), Sprime.get("diagnoses", []))
            med_j = jaccard(S.get("medications", []), Sprime.get("medications", []))

            rows.append(f"{note_id},{sym_j:.4f},{dx_j:.4f},{med_j:.4f},{int(valid)}")

            n += 1
            if n % 10 == 0:
                print(f"Round-trip processed {n} notes...")
            if args.limit and n >= args.limit:
                break

    out_csv = out_dir / "roundtrip_scores.csv"
    out_csv.write_text("\n".join(rows), encoding="utf-8")
    meta = {
        "run_started_utc": utc_now_iso(),
        "schema_sha256": schema_hash,
        "model": args.model,
        "base_url": args.base_url,
        "decoding": {"temperature": args.temperature},
        "regen_max_tokens": args.regen_max_tokens,
        "reextract_max_tokens": args.reextract_max_tokens
    }
    (out_dir / "roundtrip_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {out_csv}")

if __name__ == "__main__":
    main()