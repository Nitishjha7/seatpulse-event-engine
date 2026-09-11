"""
Translate Hinglish comments/docstrings in Python files to English.

SAFETY: every file is verified with an AST comparison after translation.
If the code structure changed at all (anything beyond comments and
docstrings), the file is left untouched and reported.
"""

import ast
import io
import os
import sys
import time

import httpx

KEY = None
for line in io.open('/app/.env', encoding='utf-8'):
    if line.startswith('GEMINI_API_KEY='):
        KEY = line.split('=', 1)[1].strip().strip('"').strip("'")

MODEL = "gemini-3.1-flash-lite"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

PROMPT = """\
You are editing a Python source file from a production codebase.

The comments and docstrings are written in "Hinglish" (romanised Hindi mixed
with English). Rewrite them in clear, professional English.

ABSOLUTE RULES:
1. Do NOT change any code. Not one identifier, string literal, number,
   import, or line of logic. Only comments (#) and docstrings change.
2. Keep every comment in the SAME position.
3. Make comments CONCISE and INFORMATIVE. The originals are verbose and
   chatty — tighten them. Explain WHY, not what the code obviously does.
   Cut filler, rhetorical questions, and repetition.
4. Keep technical terms, identifiers, SQL, and code snippets inside
   comments exactly as they are.
5. Keep markers like the warning sign, star, and arrows if present.
6. Keep references such as "Phase 7" unchanged.
7. Output ONLY the complete file content. No markdown fences, no commentary.

File: {name}

```python
{code}
```
"""


def strip_docstrings(tree):
    """Remove docstrings so the AST compares code only."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def code_fingerprint(src):
    return ast.dump(strip_docstrings(ast.parse(src)))


def translate(name, code):
    res = httpx.post(
        URL,
        headers={"x-goog-api-key": KEY},
        timeout=180,
        json={
            "contents": [{"parts": [{"text": PROMPT.format(name=name, code=code)}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 32000},
        },
    )
    res.raise_for_status()
    cand = res.json()["candidates"][0]
    out = "".join(p.get("text", "") for p in cand["content"]["parts"])
    out = out.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1]
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    out = "\n".join(line.rstrip() for line in out.splitlines())
    return out.rstrip() + "\n"


def main(paths):
    ok, skipped, failed = [], [], []
    for path in paths:
        src = io.open(path, encoding='utf-8').read()
        try:
            before = code_fingerprint(src)
        except SyntaxError as e:
            failed.append((path, f"cannot parse original: {e}"))
            continue

        try:
            out = translate(os.path.basename(path), src)
        except Exception as e:
            failed.append((path, f"api: {type(e).__name__} {str(e)[:80]}"))
            time.sleep(2)
            continue

        try:
            after = code_fingerprint(out)
        except SyntaxError as e:
            failed.append((path, f"output is not valid python: {e}"))
            continue

        if before != after:
            skipped.append(path)
            print(f"  SKIP (code changed) {path}", flush=True)
            continue

        io.open(path, 'w', encoding='utf-8', newline='\n').write(out)
        ok.append(path)
        print(f"  ok  {path}", flush=True)
        time.sleep(0.4)

    print()
    print(f"translated: {len(ok)}  |  skipped: {len(skipped)}  |  failed: {len(failed)}")
    for p in skipped:
        print("  SKIPPED:", p)
    for p, why in failed:
        print("  FAILED :", p, "-", why)


if __name__ == "__main__":
    main(sys.argv[1:])
