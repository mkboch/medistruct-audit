"""Scripted OpenAI-compatible mock endpoint (localhost only) for plumbing tests without a GPU.

Logs every request body (tools offered, messages) to --log. Behaviour, keyed on the number of tool
results already in the conversation:
  0 -> tool call list_case_artifacts(case_id)   1 -> tool call submit_audit_report(...)   2+ -> final text
Supports both streaming (SSE) and non-streaming responses.
"""
import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=18999)
ap.add_argument("--log", required=True)
A = ap.parse_args()


def find_tool(tools, suffix):
    for t in tools or []:
        n = t.get("function", {}).get("name", "")
        if n == suffix or n.endswith("__" + suffix) or n.endswith(suffix):
            return n
    return suffix


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._send(200, {"object": "list", "data": [{"id": "mock-audit", "object": "model", "max_model_len": 65536}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(A.log, "a") as f:
            f.write(json.dumps({"t": time.time(), "body": body}) + "\n")
        msgs = body.get("messages", [])
        n_tool = sum(1 for m in msgs if m.get("role") == "tool")
        text = json.dumps(msgs)
        m = re.search(r"case (\w+)", text)
        case = m.group(1) if m else "unknown"
        tools = body.get("tools")
        if n_tool == 0:
            call = {"id": "call_1", "type": "function", "function": {"name": find_tool(tools, "list_case_artifacts"),
                                                                       "arguments": json.dumps({"case_id": case})}}
            msg, finish = {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
        elif n_tool == 1:
            last = [m for m in msgs if m.get("role") == "tool"][-1]
            cid = re.search(r'"call_id":\s*"(\w+)"', json.dumps(last))
            args = {"case_id": case, "summary": "mock",
                    "findings": [{"code": "NO_ISSUES_FOUND", "category": "confirmed_finding", "target": "",
                                  "statement": "mock run", "evidence_call_ids": [cid.group(1) if cid else ""]}]}
            call = {"id": "call_2", "type": "function", "function": {"name": find_tool(tools, "submit_audit_report"),
                                                                       "arguments": json.dumps(args)}}
            msg, finish = {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
        else:
            msg, finish = {"role": "assistant", "content": "Audit submitted."}, "stop"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant"}
            if msg.get("tool_calls"):
                delta["tool_calls"] = [{**msg["tool_calls"][0], "index": 0}]
            else:
                delta["content"] = msg["content"]
            for ch in ({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                       {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}):
                ch.update(id="mock", object="chat.completion.chunk", model="mock-audit", created=int(time.time()))
                self.wfile.write(f"data: {json.dumps(ch)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(200, {"id": "mock", "object": "chat.completion", "model": "mock-audit", "created": int(time.time()),
                         "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


ThreadingHTTPServer(("127.0.0.1", A.port), H).serve_forever()
