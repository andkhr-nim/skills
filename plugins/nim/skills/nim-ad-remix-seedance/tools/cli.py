"""Tool runner: list, describe and call the tools; input and output are JSON.

    python tools/cli.py list
    python tools/cli.py describe segment_video
    python tools/cli.py run segment_video '{"video": "clip.mp4", "objects": [{"name": "a", "prompt": "woman"}]}'
    python tools/cli.py run estimate_depth @params.json      # read input from a file

Every call prints one JSON object. Failures print {"status": "failed", "error": {"code", "message", ...}}
and exit with code 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chunks  # noqa: E402
import depth  # noqa: E402
import fal_common as fal  # noqa: E402
import guide  # noqa: E402
import segment  # noqa: E402


class _Tool:
    def __init__(self, spec: dict, fn):
        self.TOOL, self.run = spec, fn


REGISTRY = {t.TOOL["name"]: t for t in (
    _Tool(segment.TOOL, segment.run),
    _Tool(depth.TOOL, depth.run),
    _Tool(guide.TOOL, guide.run),
    _Tool(chunks.PREPARE_TOOL, chunks.prepare),
    _Tool(chunks.ASSEMBLE_TOOL, chunks.assemble),
)}

_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool,
          "array": list, "object": dict}


def validate(value, schema: dict, path: str = "input") -> None:
    """Small JSON-schema subset: type, required, enum, min/max, minItems/maxItems, items, properties."""
    kind = schema.get("type")
    if isinstance(kind, str) and kind in _TYPES:
        ok = isinstance(value, _TYPES[kind]) and not (kind in ("number", "integer") and isinstance(value, bool))
        if not ok:
            raise fal.ToolError("invalid_input", f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise fal.ToolError("invalid_input", f"{path} must be one of {schema['enum']}")
    if "minimum" in schema and value < schema["minimum"]:
        raise fal.ToolError("invalid_input", f"{path} must be >= {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise fal.ToolError("invalid_input", f"{path} must be <= {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            raise fal.ToolError("invalid_input", f"{path} has {len(value)} items; allowed "
                                f"{schema.get('minItems', 0)}–{schema.get('maxItems', '∞')}")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                raise fal.ToolError("invalid_input", f"{path}.{req} is required")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k not in props:
                raise fal.ToolError("invalid_input", f"{path}.{k} is not a known parameter")
            validate(v, props[k], f"{path}.{k}")


def call(name: str, params: dict) -> dict:
    """Validate and run a tool; always returns a JSON-able dict (errors included)."""
    try:
        if name not in REGISTRY:
            raise fal.ToolError("unknown_tool", f"no tool named {name}; available: {sorted(REGISTRY)}")
        tool = REGISTRY[name]
        validate(params, tool.TOOL["input_schema"])
        return {"tool": name, **tool.run(params)}
    except fal.ToolError as e:
        return {"tool": name, **e.as_dict()}
    except Exception as e:  # unexpected: still answer in JSON
        return {"tool": name, "status": "failed", "error": {"code": "internal_error", "message": repr(e)}}


def _emit(obj: dict) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "list":
        _emit({"tools": [{"name": n, "description": m.TOOL["description"]} for n, m in REGISTRY.items()]})
        return 0
    if cmd == "describe" and len(argv) == 2:
        if argv[1] not in REGISTRY:
            _emit(fal.ToolError("unknown_tool", f"no tool named {argv[1]}").as_dict())
            return 1
        _emit(REGISTRY[argv[1]].TOOL)
        return 0
    if cmd == "run" and len(argv) == 3:
        raw = argv[2]
        try:
            params = json.loads(Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw)
        except (OSError, ValueError) as e:
            _emit({"tool": argv[1], **fal.ToolError("invalid_input", f"could not read JSON input: {e}").as_dict()})
            return 1
        result = call(argv[1], params)
        _emit(result)
        return 0 if result.get("status") == "finished" else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
