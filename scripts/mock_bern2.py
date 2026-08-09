#!/usr/bin/env python3
"""Minimal BERN2 /plain mock for lake dual-write e2e when real BERN2 is down."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode() or "{}")
        except json.JSONDecodeError:
            payload = {}
        text = str(payload.get("text") or "")
        anns = []
        for mention, obj, ids in (
            ("savolitinib", "drug", ["HMD:ENT:DC:savolitinib", "DrugBank:DB12021"]),
            ("HMPL-504", "drug", ["HMD:ENT:DC:savolitinib"]),
            ("MET", "gene", ["HMD:ENT:TGT:MET", "HGNC:7029"]),
            ("EGFR", "gene", ["HGNC:3236"]),
            ("NSCLC", "disease", ["HMD:ENT:IND:nsclc"]),
            ("non-small cell lung cancer", "disease", ["HMD:ENT:IND:nsclc"]),
        ):
            idx = text.lower().find(mention.lower())
            if idx < 0:
                continue
            anns.append(
                {
                    "mention": text[idx : idx + len(mention)],
                    "obj": obj,
                    "id": ids,
                    "prob": 0.95,
                    "span": {"begin": idx, "end": idx + len(mention)},
                }
            )
        data = json.dumps({"annotations": anns}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8888), Handler).serve_forever()
