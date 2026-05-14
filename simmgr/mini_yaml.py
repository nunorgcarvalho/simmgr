from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_BOOLS = {"true": True, "false": False}


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load the small YAML subset used by SimMgr configs.

    This intentionally supports only plain mappings, lists, scalar values, and
    inline JSON/YAML-style lists. It keeps SimMgr dependency-free on clusters
    where PyYAML is not installed.
    """

    lines = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw.rstrip())
        if line.strip():
            indent = len(line) - len(line.lstrip(" "))
            lines.append((indent, line.strip()))
    if not lines:
        return {}
    parsed, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"Could not parse YAML near line: {lines[index][1]}")
    if not isinstance(parsed, dict):
        raise ValueError("Top-level YAML document must be a mapping")
    return parsed


def dump_yaml(data: dict[str, Any]) -> str:
    return "\n".join(_dump_value(data, 0)) + "\n"


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).write_text(dump_yaml(data), encoding="utf-8")


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Unexpected indentation near: {text}")
        if text.startswith("- "):
            break
        if ":" not in text:
            raise ValueError(f"Expected key/value line, got: {text}")
        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip()
        index += 1
        if value:
            out[key] = _parse_scalar(value)
        elif index < len(lines) and lines[index][0] > current_indent:
            out[key], index = _parse_block(lines, index, lines[index][0])
        else:
            out[key] = {}
    return out, index


def _parse_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while index < len(lines):
        current_indent, text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not text.startswith("- "):
            break
        item = text[2:].strip()
        index += 1
        if not item:
            if index < len(lines) and lines[index][0] > current_indent:
                value, index = _parse_block(lines, index, lines[index][0])
            else:
                value = None
            out.append(value)
            continue
        if ":" in item and not item.startswith(("'", '"', "{", "[")):
            key, value_text = item.split(":", 1)
            value: dict[str, Any] = {}
            value[key.strip()] = _parse_scalar(value_text.strip()) if value_text.strip() else {}
            if index < len(lines) and lines[index][0] > current_indent:
                more, index = _parse_mapping(lines, index, lines[index][0])
                value.update(more)
            out.append(value)
        else:
            out.append(_parse_scalar(item))
    return out, index


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return ""
    lowered = text.lower()
    if lowered in _BOOLS:
        return _BOOLS[lowered]
    if lowered in {"null", "none", "~"}:
        return None
    if text[0:1] in {"'", '"'} and text[-1:] == text[0]:
        return text[1:-1]
    if text.startswith("[") or text.startswith("{"):
        return json.loads(_jsonify_inline(text))
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\d*\.\d+)([eE][-+]?\d+)?", text) or re.fullmatch(
        r"[-+]?\d+[eE][-+]?\d+", text
    ):
        return float(text)
    return text


def _jsonify_inline(text: str) -> str:
    # Convert simple YAML inline lists such as [foo, 1] to JSON.
    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.lower() in {"true", "false", "null"}:
            return word.lower()
        if re.fullmatch(r"[-+]?\d+(\.\d+)?", word):
            return word
        return json.dumps(word)

    if '"' in text or "'" in text:
        text = text.replace("'", '"')
    return re.sub(r"(?<=[\[,]\s)([A-Za-z_][A-Za-z0-9_./-]*)(?=\s*[,\]])|(?<=\[)([A-Za-z_][A-Za-z0-9_./-]*)(?=\s*[,\]])", lambda m: repl(m), text)


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i].rstrip()
    return line


def _dump_value(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if item == {} or item == []:
                lines.append(f"{prefix}{key}: {_format_scalar(item)}")
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_dump_value(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_format_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first = True
                for key, child in item.items():
                    lead = f"{prefix}- " if first else f"{prefix}  "
                    first = False
                    if isinstance(child, (dict, list)):
                        lines.append(f"{lead}{key}:")
                        lines.extend(_dump_value(child, indent + 4))
                    else:
                        lines.append(f"{lead}{key}: {_format_scalar(child)}")
            else:
                lines.append(f"{prefix}- {_format_scalar(item)}")
        return lines
    return [f"{prefix}{_format_scalar(value)}"]


def _format_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ":#[]{}") or text.strip() != text:
        return json.dumps(text)
    return text
