import json
from pathlib import Path


def test_object_schema_nodes_declare_items():
    schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8"))

    def check(node):
        for name, definition in node.items():
            if definition.get("type") == "object":
                assert "items" in definition, f"object schema {name!r} must declare items"
                check(definition["items"])

    check(schema)
