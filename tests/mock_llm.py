#!/usr/bin/env python3

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            body = json.dumps({"data": [{"id": "mock"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request["messages"]
        last = messages[-1]

        if last["role"] == "user" and last["content"].startswith("Reply with only DONE"):
            events = [content("DONE")]
        elif last["role"] == "user" and "tool" in last["content"]:
            arguments = json.dumps(
                {
                    "program": "printf",
                    "arguments": ["mock-tool"],
                    "reason": "Exercise local tool execution.",
                    "trust_prefix": ["printf"],
                },
                separators=(",", ":"),
            )
            midpoint = len(arguments) // 2
            events = [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "mock_call",
                                        "type": "function",
                                        "function": {
                                            "name": "run_",
                                            "arguments": arguments[:midpoint],
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "command",
                                            "arguments": arguments[midpoint:],
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            ]
        elif last["role"] == "tool":
            events = [content("tool result received")]
        else:
            events = [content("mock reply")]

        events.insert(0, {"choices": [{"delta": {"reasoning_content": "Checking the request."}}]})
        if any("ui" in message for message in messages):
            raise AssertionError("Display metadata leaked into model context")

        body = "".join(
            f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
            for event in events
        ) + "data: [DONE]\n\n"
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


def content(text):
    return {"choices": [{"delta": {"content": text}}]}


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
