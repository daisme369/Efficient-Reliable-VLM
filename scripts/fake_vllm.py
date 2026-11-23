"""Stand-in for `vllm serve` with fixed answers, to run the whole chain without a GPU. Never use it to show model quality.

Speaks the subset of vLLM's OpenAI-compatible API the backend uses: POST /v1/chat/completions with logprobs.
Answers "Yes." to questions about effusion and "No." to other yes/no questions, and returns one box on the
0-1000 grid for region prompts; every token gets log-probability ln(0.8) for answers and ln(0.6) for boxes.

    THORAX_VLM_KEY=<secret> python scripts/fake_vllm.py      # then THORAX_VLM_URL=http://127.0.0.1:8500
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import math
import os
import threading

REGION_MARK = "Return a JSON list of [x1,y1,x2,y2]"


def reply(text: str) -> tuple[str, float]:
    if REGION_MARK in text:
        return "[[150,550,450,900]]", 0.6
    if text.lower().startswith(("is ", "are ", "does ")):
        return ("Yes." if "effusion" in text.lower() else "No."), 0.8
    return "Fake model answer for testing; no model was run.", 0.8


def make_server(key: str, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802 - http.server API
            if self.path != "/v1/chat/completions":
                return self._send(404, {"error": "not found"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {key}"):
                return self._send(401, {"error": "Unauthorized"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            parts = body["messages"][-1]["content"]
            if not any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,") for p in parts):
                return self._send(400, {"error": "expected one PNG image"})
            text, probability = reply(next(p["text"] for p in parts if p.get("type") == "text"))
            tokens = [{"token": t, "logprob": math.log(probability)} for t in text.split()]
            self._send(200, {"id": "fake", "object": "chat.completion", "model": body.get("model"), "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}, "logprobs": {"content": tokens}}]})

        def log_message(self, *args):  # quiet in tests
            pass

    return ThreadingHTTPServer((host, port), Handler)


def start_in_background(key: str) -> tuple[ThreadingHTTPServer, str]:
    server = make_server(key)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    secret = os.environ.get("THORAX_VLM_KEY", "")
    if len(secret) < 16:
        raise SystemExit("Set THORAX_VLM_KEY to a random secret of at least 16 characters.")
    print("Fake vLLM on http://127.0.0.1:8500", flush=True)
    make_server(secret, port=int(os.environ.get("THORAX_VLM_PORT", "8500"))).serve_forever()
