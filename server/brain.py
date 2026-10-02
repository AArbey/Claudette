#!/usr/bin/env python3

"""Small stateful HTTP bridge between Bash clients and an OpenAI-compatible LLM."""

from __future__ import annotations

import ipaddress
import hashlib
import hmac
import base64
import io
import math
import zipfile
import xml.etree.ElementTree as ET
import json
import os
import re
import secrets
import shlex
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen



# Used only to bootstrap root runners predating /v1/update. Brain constructs every argument.
LEGACY_RUNNER_UPDATE_SCRIPT = r"""import os, subprocess, sys, tempfile, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=20) as response:
    installer = response.read(262145)
if len(installer) > 262144 or not installer.startswith(b'#!/usr/bin/env bash\n'):
    raise ValueError('Brain returned invalid installer')
descriptor, path = tempfile.mkstemp(prefix='ai-helper-runner-update-')
try:
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(installer)
    subprocess.run(['bash', path], check=True, timeout=240)
finally:
    os.unlink(path)
"""










def extract_attachment(filename: str, mime_type: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".sh", ".log", ".yaml", ".yml", ".xml", ".html", ".css")) or mime_type.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    if lower.endswith(".pdf") or mime_type == "application/pdf":
        try:
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        except ImportError as error:
            raise BrainError("PDF support unavailable; install pypdf") from error
        except Exception as error:
            raise BrainError(f"could not extract PDF text: {error}") from error
    if lower.endswith(".docx") or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                xml = archive.read("word/document.xml")
            root = ET.fromstring(xml)
            return "\n".join("".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p"))
        except Exception as error:
            raise BrainError(f"could not extract DOCX text: {error}") from error
    raise BrainError("unsupported attachment type")


def web_turn_body(body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content")
    references = body.get("references", [])
    if not isinstance(content, str) or (not content.strip() and not references):
        raise BrainError("conversation turn requires content or references")
    result = {"type": "web_user", "content": content, "references": references}
    if "branch_from" in body:
        result["branch_from"] = body["branch_from"]
    return result








if __name__ == "__main__":
    raise SystemExit(main())
