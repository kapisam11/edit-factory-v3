import json
import sys
from pathlib import Path

import jsonschema


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(config_path: Path, schema_path: Path) -> int:
    cfg = load_json(config_path)
    schema = load_json(schema_path)
    try:
        jsonschema.validate(instance=cfg, schema=schema)
        print(f"OK: {config_path} validates against {schema_path}")
        return 0
    except jsonschema.ValidationError as e:
        print(f"INVALID: {config_path} does not conform to schema:")
        print(e.message)
        return 2


def main(argv):
    if len(argv) < 3:
        print("Usage: python tools/validate_config.py <config.json> <schema.json>")
        return 1
    config_path = Path(argv[1])
    schema_path = Path(argv[2])
    return validate(config_path, schema_path)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
