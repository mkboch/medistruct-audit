# Codes/baselines/baseline_llm_notool.py
import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import requests

# -------------------------
# JSON repair helpers (copied from your run_extract.py)
# -------------------------
def extract_outer_object(s: str) -> str:
    s = (s or "").strip()
    a = s.find("{")
    b = s.rfind("}")
    if a != -1 and b != -1 and b > a:
        return s[a : b + 1]
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

# -------------------------
# LLM no-tool baseline
# -------------------------
def llm_extract_notool(
    model: str,
    prompt: str,
    max_tokens: int = 500,
    temperature: float = 0.0,
    timeout_s: int = 60,
    retries: int = 2,
) -> Tuple[Dict[str, Any], str]:
    """
    Returns (parsed_obj, raw_text).
    - No tools, no tool_choice.
    - Asks model to output JSON in plain text.
    """
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")
    if not base_url:
        raise RuntimeError("OPENAI_BASE_URL not set")
    url = base_url + "/chat/completions"

    sys_msg = (
        "Return ONLY a JSON object with keys: symptoms (list[str]), diagnoses (list[str]), "
        "medications (list[str]), assessment_summary (string). "
        "No markdown. No extra text. Strict JSON only."
    )

    payload = {
        "model": model,
        "temperature": float(temperature),
        "top_p": 1.0,
        "max_tokens": int(max_tokens),
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    last_err: Optional[Exception] = None
    for a in range(1, retries + 1):
        try:
            r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=timeout_s)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:800]}")
            data = r.json()
            msg = data["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            fixed = repair_json_text(text)
            obj = json.loads(fixed)
            return obj, text
        except Exception as e:
            last_err = e
            time.sleep(1.5 * a)

    raise RuntimeError(str(last_err))