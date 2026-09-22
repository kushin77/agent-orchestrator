"""The stdlib-only YAML subset loader both adapters share (#1208).

The loader covers mappings, sequences, scalars, quoted strings and block
scalars — enough for the fleet's own YAML — because neither adapter may take a
third-party dependency (the seam is stdlib-only by construction, so the gate and
the tests never need the network or a package install).

The body is the **union** of the two copies it replaces: the block parser both
adapters had, plus the flow-mapping/flow-sequence support
``gateway/finops/tiers.yaml`` needs (``code-author: { capability: code-author,
defaultTier: L0, maxTier: L1 }``), which only hermes's copy carried. Paperclip's
copy parsed an inline ``[a, b]`` sequence with a bare ``split(",")``; the union
parses it with the quote- and depth-aware :func:`_split_flow`, which is what
keeps a nested or quoted element from being cut in half. For every input either
adapter actually reads the two agree — proven by the adapters' own suites, whose
frozen outputs are byte-identical across the extraction.

---knowledge---
module_id: integrations._seam.yaml_subset
system: integrations
app: seam
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [load_yaml, load_yaml_file]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _strip_comment(text: str) -> str:
    out: List[str] = []
    quote = ""
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _tokenize(text: str) -> List[Tuple[int, str]]:
    """Flatten YAML into ``(indent, content)`` items, dropping comments/blanks.

    A block-scalar header (``key: |`` / ``key: >``) is kept as an empty scalar
    and its body is skipped, so free text inside a description can never be
    mis-read as structure.
    """
    lines = text.split("\n")
    tokens: List[Tuple[int, str]] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.lstrip(" ")
        if not stripped.strip() or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(stripped)
        content = _strip_comment(stripped).rstrip()
        if content.endswith((": |", ": |-", ": >", ": >-")):
            key = content.split(":", 1)[0].strip()
            tokens.append((indent, f'{key}: ""'))
            i += 1
            while i < len(lines):
                nxt = lines[i]
                nstripped = nxt.lstrip(" ")
                if not nstripped.strip():
                    i += 1
                    continue
                if (len(nxt) - len(nstripped)) <= indent:
                    break
                i += 1
            continue
        tokens.append((indent, content))
        i += 1
    return tokens


def _split_kv(content: str) -> Optional[Tuple[str, str]]:
    if content.startswith(("'", '"')):
        return None
    for idx, ch in enumerate(content):
        if ch != ":":
            continue
        if idx + 1 < len(content) and content[idx + 1] != " ":
            return None
        key = content[:idx].strip()
        if not key or " " in key:
            return None
        return key, content[idx + 1:].strip()
    return None


def _split_flow(inner: str) -> List[str]:
    """Split a flow body on top-level commas, quote- and depth-aware."""
    parts: List[str] = []
    depth = 0
    quote = ""
    cur: List[str] = []
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_flow(text: str) -> Any:
    """Parse one flow mapping / sequence (``{ k: v }``, ``[ a, b ]``).

    ``gateway/finops/tiers.yaml`` writes its ``taskClasses`` values as inline
    flow mappings (``code-author: { capability: code-author, defaultTier: L0,
    maxTier: L1 }``), which the block parser above does not cover; this reads
    them as real mappings so the tier projection can be deterministic.
    """
    inner = text[1:-1].strip()
    if text.startswith("{"):
        if not inner:
            return {}
        out: Dict[str, Any] = {}
        for part in _split_flow(inner):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                key, value = part.split(":", 1)
                out[key.strip()] = _scalar(value.strip())
            else:
                out[part] = None
        return out
    if not inner:
        return []
    return [_scalar(part.strip()) for part in _split_flow(inner) if part.strip()]


def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "" or text in ("null", "~"):
        return None
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if len(text) >= 2 and text[0] in "{[" and text[-1] in "}]":
        return _parse_flow(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_map(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[Dict[str, Any], int]:
    mapping: Dict[str, Any] = {}
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"unexpected indent {ind} (want {indent}): {content!r}")
        if content.startswith("- "):
            break
        kv = _split_kv(content)
        if kv is None:
            raise ValueError(f"not a mapping entry: {content!r}")
        key, value = kv
        idx += 1
        if value == "":
            if idx < len(tokens) and tokens[idx][0] > indent:
                nested, idx = _parse_block(tokens, idx, tokens[idx][0])
                mapping[key] = nested
            else:
                mapping[key] = None
        else:
            mapping[key] = _scalar(value)
    return mapping, idx


def _parse_seq(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[List[Any], int]:
    seq: List[Any] = []
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"unexpected indent {ind} (want {indent}): {content!r}")
        if not content.startswith("- "):
            break
        rest = content[2:].strip()
        idx += 1
        if rest == "":
            if idx < len(tokens) and tokens[idx][0] > indent:
                nested, idx = _parse_block(tokens, idx, tokens[idx][0])
                seq.append(nested)
            else:
                seq.append(None)
            continue
        kv = _split_kv(rest)
        if kv is None:
            seq.append(_scalar(rest))
            continue
        sub_indent = indent + 2
        entry: Dict[str, Any] = {}
        key, value = kv
        if value == "" and idx < len(tokens) and tokens[idx][0] > sub_indent:
            nested, idx = _parse_block(tokens, idx, tokens[idx][0])
            entry[key] = nested
        else:
            entry[key] = _scalar(value) if value != "" else None
        while idx < len(tokens):
            ind2, content2 = tokens[idx]
            if ind2 < sub_indent or content2.startswith("- "):
                break
            if ind2 > sub_indent:
                raise ValueError(f"unexpected indent {ind2} in mapping: {content2!r}")
            kv2 = _split_kv(content2)
            if kv2 is None:
                break
            key2, value2 = kv2
            idx += 1
            if value2 == "" and idx < len(tokens) and tokens[idx][0] > sub_indent:
                nested2, idx = _parse_block(tokens, idx, tokens[idx][0])
                entry[key2] = nested2
            else:
                entry[key2] = _scalar(value2) if value2 != "" else None
        seq.append(entry)
    return seq, idx


def _parse_block(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[Any, int]:
    if tokens[idx][1].startswith("- "):
        return _parse_seq(tokens, idx, indent)
    return _parse_map(tokens, idx, indent)


def load_yaml(text: str) -> Any:
    """Load the YAML subset the fleet's own files use."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    return _parse_block(tokens, 0, tokens[0][0])[0]


def load_yaml_file(path: Path) -> Any:
    return load_yaml(path.read_text(encoding="utf-8"))
