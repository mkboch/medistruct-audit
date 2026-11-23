"""Tool-calling compatibility suite for a candidate audit LLM served by vLLM (OpenAI-compatible API).

Each probe is repeated N times (sampling as served). Writes JSON results to --out.
Usage: python tests/test_toolcalling.py --port 8120 --model qwen36-audit --n 5 --out runs/model_selection/toolcalling.json
"""
import argparse
import json
import time
import urllib.error
import urllib.request

TOOLS = [
    {"type": "function", "function": {"name": "get_metric", "description": "Return a stored evaluation metric by name.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                                                     "required": ["name"]}}},
    {"type": "function", "function": {"name": "count_records", "description": "Count records in a named split.",
                                      "parameters": {"type": "object", "properties": {"split": {"type": "string", "enum": ["valid", "failed"]},
                                                                                      "include_malformed": {"type": "boolean"}},
                                                     "required": ["split", "include_malformed"]}}},
]


def post(port, body, timeout=300, stream=False):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if not stream:
            return json.load(r)
        chunks = [l.decode()[6:] for l in r if l.startswith(b"data: ") and not l.startswith(b"data: [DONE]")]
        return [json.loads(c) for c in chunks]


def msg(d):
    return d["choices"][0]["message"]


def run(port, model, n):
    base = {"model": model, "max_tokens": 4096}
    res = {}

    def probe(name, fn):
        out = []
        for _ in range(n):
            t = time.time()
            try:
                ok, info = fn()
            except Exception as e:
                ok, info = False, f"exception: {type(e).__name__}: {e}"[:300]
            out.append({"ok": ok, "info": info, "s": round(time.time() - t, 2)})
        res[name] = {"pass_rate": sum(o["ok"] for o in out) / n, "trials": out}
        print(f"{name:34s} {res[name]['pass_rate']:.2f}")

    def chat():
        m = msg(post(port, {**base, "messages": [{"role": "user", "content": "Reply with the single word READY."}]}))
        return "READY" in (m.get("content") or ""), {"content": (m.get("content") or "")[:80], "has_reasoning": bool(m.get("reasoning") or m.get("reasoning_content"))}

    def required():
        m = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "required",
                            "messages": [{"role": "user", "content": "What is the coverage metric?"}]}))
        tc = m.get("tool_calls") or []
        return bool(tc) and tc[0]["function"]["name"] == "get_metric", [t["function"] for t in tc]

    def auto_select():
        m = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "auto",
                            "messages": [{"role": "user", "content": "How many failed records are there, excluding malformed ones? Use a tool."}]}))
        tc = m.get("tool_calls") or []
        if not tc:
            return False, {"content": (m.get("content") or "")[:120]}
        f = tc[0]["function"]
        args = json.loads(f["arguments"])
        return f["name"] == "count_records" and args == {"split": "failed", "include_malformed": False}, f

    def no_tool_needed():
        m = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "auto",
                            "messages": [{"role": "user", "content": "What is 2 + 3? Answer directly without tools."}]}))
        return not m.get("tool_calls") and "5" in (m.get("content") or ""), {"tool_calls": bool(m.get("tool_calls")), "content": (m.get("content") or "")[:60]}

    def multi_step():
        msgs = [{"role": "system", "content": "Use tools to get facts. Never invent numbers."},
                {"role": "user", "content": "Get the coverage metric and the number of valid records (excluding malformed), then report both numbers."}]
        fake = {"get_metric": json.dumps({"name": "coverage", "value": 0.5767}),
                "count_records": json.dumps({"split": "valid", "count": 4994})}
        called = []
        for step in range(6):
            m = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "auto", "messages": msgs}))
            tcs = m.get("tool_calls") or []
            if not tcs:
                txt = m.get("content") or ""
                return ({"get_metric", "count_records"} <= set(called) and "0.5767" in txt and "4994" in txt.replace(",", "")), {"called": called, "final": txt[:200]}
            msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": tcs})
            for tc in tcs:
                called.append(tc["function"]["name"])
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": fake.get(tc["function"]["name"], "{}")})
        return False, {"called": called, "final": "no final answer within 6 steps"}

    def tool_error_recovery():
        msgs = [{"role": "user", "content": "Get the metric named 'coverage'."}]
        m = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "auto", "messages": msgs}))
        tcs = m.get("tool_calls") or []
        if not tcs:
            return False, "no tool call"
        msgs += [{"role": "assistant", "content": m.get("content"), "tool_calls": tcs},
                 {"role": "tool", "tool_call_id": tcs[0]["id"], "content": json.dumps({"error": "tool failure: metric store unavailable"})}]
        m2 = msg(post(port, {**base, "tools": TOOLS, "tool_choice": "none", "messages": msgs}))
        txt = (m2.get("content") or "").lower()
        import re
        fabricated = bool(re.search(r"\d+\.\d+|\d+\s*%", txt))  # any metric-like number = invented value
        return ("unavailable" in txt or "fail" in txt or "error" in txt or "could not" in txt) and not fabricated, txt[:200]

    def streaming():
        ch = post(port, {**base, "stream": True, "tools": TOOLS, "tool_choice": "auto",
                         "messages": [{"role": "user", "content": "What is the coverage metric? Use a tool."}]}, stream=True)
        names = [tc.get("function", {}).get("name") for c in ch for tc in (c["choices"][0]["delta"].get("tool_calls") or []) if c.get("choices")]
        args = "".join(tc.get("function", {}).get("arguments") or "" for c in ch for tc in (c["choices"][0]["delta"].get("tool_calls") or []) if c.get("choices"))
        ok = "get_metric" in names
        try:
            ok = ok and json.loads(args).get("name") == "coverage"
        except Exception:
            ok = False
        return ok, {"names": [n for n in names if n], "args": args[:100]}

    def timeout_handling():
        try:
            post(port, {**base, "max_tokens": 4096, "messages": [{"role": "user", "content": "Write a 3000-word essay."}]}, timeout=2)
            return False, "no timeout raised"
        except Exception as e:
            return True, type(e).__name__

    for name, fn in [("chat", chat), ("required_tool_call", required), ("auto_tool_selection_and_args", auto_select),
                     ("no_tool_when_unnecessary", no_tool_needed), ("multi_step_sequence", multi_step),
                     ("tool_error_no_fabrication", tool_error_recovery), ("streaming_tool_call", streaming),
                     ("client_timeout_handling", timeout_handling)]:
        probe(name, fn)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = run(a.port, a.model, a.n)
    from pathlib import Path
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"port": a.port, "model": a.model, "n": a.n, "results": r}, indent=1))
