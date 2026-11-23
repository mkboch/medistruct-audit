import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

import requests

from utils import (
    utc_now_iso,
    sha256_text,
    sha256_file,
    build_extraction_view,
    normalize_structured,
    validate_schema,
    evidence_span_checks,
)


# =========================
# Deterministic evidence
# =========================
def generate_evidence_spans(note_text: str, structured: Dict[str, Any], cap_total: int = 12, cap_meds: int = 4):
    """
    Deterministic evidence:
    For each extracted item, find first case-insensitive occurrence in note_text.
    Create (start,end) span for the matched substring.
    Caps total evidence and medication evidence to control size.
    """
    text = note_text or ""
    low = text.lower()

    evidence = []
    used = set()

    def add(field: str, item: str):
        nonlocal evidence
        if len(evidence) >= cap_total:
            return
        needle = (item or "").strip().lower()
        if not needle:
            return
        key = (field, needle)
        if key in used:
            return
        idx = low.find(needle)
        if idx == -1:
            return
        start = idx
        end = idx + len(item.strip())
        evidence.append({"field": field, "item": item, "start": start, "end": end})
        used.add(key)

    for field in ["symptoms", "diagnoses"]:
        for item in structured.get(field, []) or []:
            if isinstance(item, str) and item.strip():
                add(field, item)

    meds_added = 0
    for item in structured.get("medications", []) or []:
        if len(evidence) >= cap_total or meds_added >= cap_meds:
            break
        if isinstance(item, str) and item.strip():
            before = len(evidence)
            add("medications", item)
            if len(evidence) > before:
                meds_added += 1

    return evidence


# =========================
# IO helpers
# =========================
def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, note_text: str) -> str:
    return template.replace("<<<NOTE_TEXT>>>", note_text)


def ensure_dirs(out_dir: Path) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "raw": out_dir / "raw",
        "norm": out_dir / "normalized",
        "receipt": out_dir / "receipts",
        "views": out_dir / "views",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


# =========================
# Robust JSON repair helpers
# =========================
def extract_outer_object(s: str) -> str:
    s = (s or "").strip()
    a = s.find("{")
    b = s.rfind("}")
    if a != -1 and b != -1 and b > a:
        return s[a: b + 1]
    return s


def remove_newlines_inside_strings(s: str) -> str:
    out = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            out.append(ch)
            in_str = not in_str
            continue
        if in_str and ch in "\r\n":
            continue
        out.append(ch)
    return "".join(out)


def escape_unescaped_quotes_inside_strings(s: str) -> str:
    out = []
    in_str = False
    esc = False

    def next_nonspace(i: int) -> str:
        j = i
        while j < len(s) and s[j] in " \t\r\n":
            j += 1
        return s[j] if j < len(s) else ""

    i = 0
    while i < len(s):
        ch = s[i]

        if esc:
            out.append(ch)
            esc = False
            i += 1
            continue

        if ch == "\\":
            out.append(ch)
            esc = True
            i += 1
            continue

        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
            else:
                nxt = next_nonspace(i + 1)
                if nxt in [",", ":", "}", "]"]:
                    in_str = False
                    out.append(ch)
                else:
                    out.append('\\"')
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def remove_trailing_commas(s: str) -> str:
    out = []
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            out.append(ch)
            in_str = not in_str
            continue
        if not in_str and ch == ",":
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] in "}]":
                continue
        out.append(ch)
    return "".join(out)


def repair_json_text(s: str) -> str:
    s = extract_outer_object(s)
    s = remove_newlines_inside_strings(s)
    s = escape_unescaped_quotes_inside_strings(s)
    s = remove_trailing_commas(s)
    return s


# =========================
# HTTP client
# =========================
def make_session() -> requests.Session:
    """
    Reuse TCP connections to reduce "connection refused"/timeouts when running long jobs.
    """
    sess = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=0)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


def _openai_url() -> str:
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("OPENAI_BASE_URL not set")
    return base_url + "/chat/completions"


def _headers() -> Dict[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}


def fetch_json_schema_http(
    session: requests.Session,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    schema: Dict[str, Any],
    timeout_s: int,
    retries: int,
    note_id: str = "",
) -> Tuple[str, int]:
    """
    Returns (json_text, http_attempts_used).

    Uses response_format=json_schema, so JSON is returned in message.content.
    This avoids tool-calling parser issues and reduces JSON breakage.
    """
    url = _openai_url()
    headers = _headers()

    payload = {
        "model": model,
        "temperature": temperature,
        "top_p": 1.0,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "structured_report", "schema": schema},
        },
        "messages": [
            {"role": "system", "content": "Return ONLY valid JSON that matches the schema. No extra text."},
            {"role": "user", "content": prompt},
        ],
    }

    last_err: Optional[Exception] = None

    def attempt_timeout(attempt: int) -> int:
        # First attempt full; later attempts shorter to prevent feeling stuck.
        if attempt <= 1:
            return timeout_s
        return max(20, int(timeout_s * 0.5))

    for attempt in range(1, retries + 1):
        tmo = attempt_timeout(attempt)
        try:
            if note_id:
                print(f"[HTTP json_schema] note={note_id} attempt={attempt}/{retries} timeout={tmo}s")

            # Tuple timeout = (connect_timeout, read_timeout)
            r = session.post(url, headers=headers, data=json.dumps(payload), timeout=(10, tmo))
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1200]}")
            data = r.json()
            msg = data["choices"][0].get("message", {})
            content = (msg.get("content", "") or "").strip()
            if not content:
                raise RuntimeError("Empty message.content from server.")
            return content, attempt

        except Exception as e:
            last_err = e
            time.sleep(2 * attempt)

    raise RuntimeError(str(last_err))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", required=True)

    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max_tokens", type=int, default=500)

    ap.add_argument("--max_view_chars", type=int, default=26000)
    ap.add_argument("--retry_max_view_chars", type=int, default=16000)
    ap.add_argument("--retry_max_tokens", type=int, default=900)

    ap.add_argument("--http_timeout", type=int, default=60)
    ap.add_argument("--http_retries", type=int, default=4)

    ap.add_argument("--limit", type=int, default=0)

    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    paths = ensure_dirs(out_dir)

    schema_path = Path(args.schema)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_hash = sha256_file(schema_path)

    prompt_path = Path(args.prompt)
    prompt_template = load_text(prompt_path)
    prompt_hash = sha256_file(prompt_path)

    inputs_path = Path(args.inputs)

    run_meta = {
        "run_started_utc": utc_now_iso(),
        "inputs": str(inputs_path),
        "schema_path": str(schema_path),
        "schema_sha256": schema_hash,
        "prompt_path": str(prompt_path),
        "prompt_sha256": prompt_hash,
        "model": args.model,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "decoding": {"temperature": args.temperature, "top_p": 1.0, "max_tokens": args.max_tokens},
        "max_view_chars": args.max_view_chars,
        "retry_max_view_chars": args.retry_max_view_chars,
        "retry_max_tokens": args.retry_max_tokens,
        "http_timeout": args.http_timeout,
        "http_retries": args.http_retries,
        "resume_skip_existing_receipts": True,
        "llm_mode": "json_schema",
    }
    (out_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")

    session = make_session()

    processed_new = 0
    skipped = 0
    receipts_written = 0  # track without glob() each time

    with inputs_path.open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            note_id = str(rec["note_id"])

            receipt_path = paths["receipt"] / f"{note_id}.json"
            if receipt_path.exists():
                skipped += 1
                continue

            subject_id = int(rec["subject_id"])
            hadm_id = rec.get("hadm_id", None)
            note_text_full = str(rec["text"])
            note_hash = sha256_text(note_text_full)

            # Attempt 1 view
            view_text, view_meta = build_extraction_view(note_text_full, max_chars=args.max_view_chars)
            (paths["views"] / f"{note_id}.txt").write_text(view_text, encoding="utf-8")
            prompt = render_prompt(prompt_template, view_text)

            parse_ok = True
            parse_err = ""
            parsed_obj: Dict[str, Any] = {}
            http_attempts_used = 0

            raw_record = {
                "llm_mode": "json_schema",
                "attempt": 1,
                "http_attempts_used": None,
                "message_content": None,
                "error": None,
            }

            try:
                # Attempt 1: json_schema
                content, http_attempts_used = fetch_json_schema_http(
                    session=session,
                    model=args.model,
                    prompt=prompt,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                    schema=schema,
                    timeout_s=args.http_timeout,
                    retries=args.http_retries,
                    note_id=note_id,
                )
                raw_record["http_attempts_used"] = http_attempts_used
                raw_record["message_content"] = content

                (paths["raw"] / f"{note_id}.txt").write_text(
                    json.dumps(raw_record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                try:
                    fixed = repair_json_text(content)
                    parsed_obj = json.loads(fixed)
                except Exception as e1:
                    # Attempt 2: smaller view + more tokens
                    view_text2, view_meta2 = build_extraction_view(
                        note_text_full, max_chars=min(args.max_view_chars, args.retry_max_view_chars)
                    )
                    (paths["views"] / f"{note_id}.txt").write_text(view_text2, encoding="utf-8")
                    prompt2 = render_prompt(prompt_template, view_text2)

                    content2, http_attempts_used2 = fetch_json_schema_http(
                        session=session,
                        model=args.model,
                        prompt=prompt2,
                        max_tokens=args.retry_max_tokens,
                        temperature=args.temperature,
                        schema=schema,
                        timeout_s=args.http_timeout,
                        retries=args.http_retries,
                        note_id=note_id,
                    )

                    raw_record2 = {
                        "llm_mode": "json_schema",
                        "attempt": 2,
                        "prev_error": str(e1),
                        "http_attempts_used": http_attempts_used2,
                        "message_content": content2,
                        "error": None,
                    }
                    (paths["raw"] / f"{note_id}.txt").write_text(
                        json.dumps(raw_record2, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    fixed2 = repair_json_text(content2)
                    parsed_obj = json.loads(fixed2)

                    view_meta = dict(view_meta)
                    view_meta["retry_attempted"] = True
                    view_meta["retry_max_chars"] = args.retry_max_view_chars

            except Exception as e:
                parse_ok = False
                parse_err = str(e)

                raw_record_fail = {
                    "llm_mode": "json_schema",
                    "attempt": "failed",
                    "http_attempts_used": http_attempts_used,
                    "message_content": raw_record.get("message_content"),
                    "error": parse_err,
                }
                (paths["raw"] / f"{note_id}.txt").write_text(
                    json.dumps(raw_record_fail, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                parsed_obj = {}

            norm = normalize_structured(parsed_obj)

            # Deterministic bounded evidence
            norm["evidence"] = generate_evidence_spans(view_text, norm, cap_total=12, cap_meds=4)

            valid, schema_errs = validate_schema(norm, schema)

            norm_path = paths["norm"] / f"{note_id}.json"
            norm_path.write_text(json.dumps(norm, indent=2, ensure_ascii=False), encoding="utf-8")

            ev_metrics = evidence_span_checks(view_text, norm)

            receipt = {
                "note_id": note_id,
                "subject_id": subject_id,
                "hadm_id": hadm_id,
                "note_type": "discharge",
                "note_sha256": note_hash,
                "schema_sha256": schema_hash,
                "prompt_sha256": prompt_hash,
                "model": args.model,
                "base_url": os.environ.get("OPENAI_BASE_URL"),
                "decoding": {"temperature": args.temperature, "top_p": 1.0, "max_tokens": args.max_tokens},
                "timestamps": {"extracted_utc": utc_now_iso()},
                "input_preprocessing": {"extraction_view": view_meta},
                "artifacts": {
                    "view_path": str(paths["views"] / f"{note_id}.txt"),
                    "raw_output_path": str(paths["raw"] / f"{note_id}.txt"),
                    "normalized_json_path": str(norm_path),
                },
                "parse": {"json_ok": parse_ok, "error": parse_err, "used_toolcalls": False, "llm_mode": "json_schema"},
                "schema_validation": {"is_valid": valid, "errors": schema_errs},
                "evidence_metrics": ev_metrics,
            }

            receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            receipts_written += 1

            processed_new += 1
            if processed_new % 20 == 0:
                print(f"[PROGRESS] new={processed_new} skipped={skipped} receipts_written_this_run={receipts_written}")

            if args.limit and processed_new >= args.limit:
                break

    print(f"Done. Processed {processed_new} new notes (skipped {skipped}). Outputs in {out_dir}")


if __name__ == "__main__":
    main()