#!/usr/bin/env python3
"""Check every API call in this action against InfraNest's published API description.

Every example, in every language, calls the API through one small helper, so the calls can be read reliably:

  sh          infranest METHOD "/path" "$body"              (body built by `body=$(jq -n '{…}')`)
  Python      infranest("METHOD", f"/path", {"field": …})
  JavaScript  infranest('METHOD', `/path`, { field: … })
  PowerShell  Invoke-InfraNest -Method METHOD -Path "/path" -Body @{ field = … }

The method and path must be a public operation, and every field a request body sends must be one that operation
documents. A renamed endpoint or field fails here instead of in somebody's deploy pipeline.

Usage: check-requests.py [openapi.json]   (default: download https://developers.infranest.io/openapi.json)
Exit 0 when everything matches, or when the description cannot be fetched (a warning, so a docs outage does not
block this repository); exit 1 on a mismatch.
"""
import json
import pathlib
import re
import sys
import urllib.request

URL = "https://developers.infranest.io/openapi.json"
CALL = re.compile(r'infranest\s+(GET|POST|PUT|PATCH|DELETE)\s+"([^"]+)"(?:\s+"\$(\w+)")?')
JQ_BODY = re.compile(r"""(\w+)=\$\(jq\s.*?'(\{.*?\})'\)""", re.S)
KEY = re.compile(r"[{,]\s*([a-z_]+)\s*:")
ASSIGN = re.compile(r'^\s*([A-Z_]+)="(/[^"]*)"', re.M)
PY_CALL = re.compile(r'infranest\(\s*"(GET|POST|PUT|PATCH|DELETE)"\s*,\s*f?"([^"]+)"(\s*,\s*\{.*?\})?', re.S)
JS_CALL = re.compile(r"infranest\(\s*'(GET|POST|PUT|PATCH|DELETE)'\s*,\s*[`'\"]([^`'\"]+)[`'\"](\s*,\s*\{.*?\})?", re.S)
PS_CALL = re.compile(r'Invoke-InfraNest\s+-Method\s+(GET|POST|PUT|PATCH|DELETE)\s+-Path\s+"([^"]+)"(\s+-Body\s+@\{.*?\})?', re.S)


def literal_keys(literal, language):
    """The top-level field names of a body literal: a Python dict, a JavaScript object or a PowerShell hashtable."""
    inner = literal[literal.index("{") + 1:literal.rindex("}")] if "}" in literal else literal
    if language == "ps":
        return set(re.findall(r"(?:^|[;\n{])\s*([a-z_]+)\s*=", inner))
    if language == "py":
        return set(re.findall(r'"([a-z_]+)"\s*:', inner))
    keys = set()
    for item in re.split(r",(?![^{\[]*[}\]])", inner):  # top-level items only
        item = item.strip()
        match = re.match(r"([a-z_]+)\s*(?::|$)", item)
        if match:
            keys.add(match.group(1))
    return keys


def expand(path, assignments):
    """Expand a leading $NAME the script assigned (ORG="/organizations/$INFRANEST_ORG"), repeatedly."""
    for _ in range(5):
        match = re.match(r"\$(\w+)", path)
        if not match or match.group(1) not in assignments:
            break
        path = assignments[match.group(1)] + path[match.end():]
    return path


def load_spec():
    if len(sys.argv) > 1:
        return json.loads(pathlib.Path(sys.argv[1]).read_text())
    try:
        with urllib.request.urlopen(URL, timeout=20) as answer:
            return json.load(answer)
    except Exception as error:  # noqa: BLE001 — any failure to fetch is a skip, said loudly
        print(f"::warning::Could not fetch {URL} ({error}); request check skipped.")
        sys.exit(0)


def operation(spec, method, path):
    """The public operation whose path template matches: literal segments equal, {param} or $VAR any segment."""
    path = path.split("?", 1)[0]
    segments = [s for s in path.strip("/").split("/") if s]
    for template, operations in spec.get("paths", {}).items():
        parts = [p for p in template.strip("/").split("/") if p][1:]  # drop the leading "api"
        if len(parts) != len(segments) or method.lower() not in operations:
            continue
        if all(p.startswith("{") or "$" in s or "{" in s or p == s for p, s in zip(parts, segments)):
            return operations[method.lower()]
    return None


def main():
    spec = load_spec()
    problems, checked = [], 0
    for script in sorted(p for p in pathlib.Path(".").rglob("*") if p.suffix in (".sh", ".py", ".mjs", ".ps1")):
        text = script.read_text()
        calls = []  # (method, path, body fields or None)
        if script.suffix == ".sh":
            assignments = dict(ASSIGN.findall(text))
            bodies = {}
            for match in JQ_BODY.finditer(text):
                bodies.setdefault(match.group(1), []).append((match.start(), set(KEY.findall(match.group(2)))))
            for call in CALL.finditer(text):
                method, path, body_var = call.groups()
                fields = None
                if body_var:
                    # The nearest `body=$(jq … '{…}')` above this call.
                    fields = next((keys for pos, keys in reversed(bodies.get(body_var, [])) if pos < call.start()), set())
                calls.append((method, expand(path, assignments), fields))
        else:
            language, pattern = {".py": ("py", PY_CALL), ".mjs": ("js", JS_CALL), ".ps1": ("ps", PS_CALL)}[script.suffix]
            for call in pattern.finditer(text):
                method, path, body = call.groups()
                calls.append((method, path, literal_keys(body, language) if body else None))

        for method, path, fields in calls:
            checked += 1
            where = f"{script}: {method} {path}"
            op = operation(spec, method, path)
            if op is None:
                problems.append(f"{where} — not a public operation in the API description")
                continue
            if fields:
                documented = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema", {}).get("properties", {})
                problems += [f"{where} — sends \"{f}\", which the operation does not document" for f in sorted(fields) if f not in documented]
    if checked < 5:
        problems.append(f"Read only {checked} calls — is the parser still matching the scripts?")
    for problem in problems:
        print(f"::error::{problem}")
    print(f"Checked {checked} API calls in run.sh: {'all match' if not problems else f'{len(problems)} problem(s)'}.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
