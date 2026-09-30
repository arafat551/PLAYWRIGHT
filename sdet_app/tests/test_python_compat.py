"""Compatibility guard: the production server runs an older Python than the dev
machine. This test catches source constructs that the server cannot parse.

The main trap is a non-triple-quoted f-string that spans several physical lines.
Before Python 3.12 (PEP 701) the tokenizer reads the whole f-string as a single
string token, so any newline inside it -- even inside a {...} expression -- is a
hard ``SyntaxError: EOL while scanning string literal``. The exact string is
produced only at runtime, so the crash appears on the server at deploy time and
never in the test suite.
"""

import io
import os
import re
import tokenize

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Highest Python version the production server is known to run. Syntax newer
# than this must not be introduced.
SERVER_MAX_PYTHON = (3, 11)

_FSTRING_OPENER = re.compile(r"""(?<![A-Za-z0-9_])f(['"])(?!\1)""")

# Constructs that need Python >= 3.12 (PEP 695 / PEP 701).
_PY312_PATTERNS = (
    (re.compile(r"^\s*type\s+\w+\s*="), "PEP 695 `type` statement (Python 3.12+)"),
    (re.compile(r"^\s*(?:async\s+)?def\s+\w+\s*\[[^\]]"), "PEP 695 generic function (Python 3.12+)"),
    (re.compile(r"^\s*class\s+\w+\s*\[[^\]]"), "PEP 695 generic class (Python 3.12+)"),
)


def _iter_sources():
    for dirpath, dirnames, filenames in os.walk(APP_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw.decode("utf-8", errors="replace")


def _multiline_fstrings(text):
    """Return [(line, snippet)] for every non-triple f-string holding a newline."""
    found = []
    for match in _FSTRING_OPENER.finditer(text):
        quote = match.group(1)
        i = match.end()
        while i < len(text):
            char = text[i]
            if char == "\\":
                i += 2
                continue
            if char in ("\n", "\r"):
                line = text.count("\n", 0, match.start()) + 1
                snippet = text[match.end():match.end() + 60].replace("\n", "\\n")
                found.append((line, snippet))
                break
            if char == quote:
                break
            i += 1
    return found


def _py312_syntax(text):
    """Return [(line, snippet, rule)] for syntax that requires Python 3.12+."""
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, rule in _PY312_PATTERNS:
            if pattern.match(line):
                found.append((lineno, line.strip()[:60], rule))
    return found


def test_no_multiline_fstrings():
    """F-strings must stay on one line so older interpreters can parse them."""
    offenders = []
    for path in _iter_sources():
        rel = os.path.relpath(path, os.path.dirname(APP_ROOT))
        for lineno, snippet in _multiline_fstrings(_read(path)):
            offenders.append(f"{rel}:{lineno}  {snippet}")
    assert not offenders, (
        "F-string multi-lignes detecte(s) — invalide avant Python 3.12 "
        f"(serveur de production = Python {'.'.join(map(str, SERVER_MAX_PYTHON))}).\n"
        "Corrige en sortant l'expression du f-string :\n"
        "    valeur = ma_fonction(\n"
        "        'texte long '\n"
        "        'suite', 'warning')\n"
        "    contenu = f'...{valeur}...'\n\n"
        + "\n".join(offenders)
    )


def test_no_python_312_syntax():
    """Nothing may use syntax newer than the production interpreter."""
    offenders = []
    for path in _iter_sources():
        rel = os.path.relpath(path, os.path.dirname(APP_ROOT))
        for lineno, snippet, rule in _py312_syntax(_read(path)):
            offenders.append(f"{rel}:{lineno}  {snippet}   <- {rule}")
    assert not offenders, (
        "Syntaxe Python 3.12+ detectee — le serveur de production ne peut pas "
        "l'interpreter.\n\n" + "\n".join(offenders)
    )


def test_all_sources_tokenize_cleanly():
    """Every module must tokenize without error under the running interpreter."""
    broken = []
    for path in _iter_sources():
        try:
            with open(path, "rb") as fh:
                list(tokenize.tokenize(io.BytesIO(fh.read()).readline))
        except (tokenize.TokenError, SyntaxError, IndentationError) as exc:
            rel = os.path.relpath(path, os.path.dirname(APP_ROOT))
            broken.append(f"{rel}  ->  {type(exc).__name__}: {exc}")
    assert not broken, "Fichier(s) illisible(s) :\n" + "\n".join(broken)
