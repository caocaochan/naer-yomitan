"""Check a built dictionary before it is published.

Validates index.json and a sample of term entries against Yomitan's own JSON schemas (fetched from the
Yomitan repository), and checks that the packaged index.json matches the repo's index.json.

    python validate.py [dist/naer_terms.zip]
"""

import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent
SCHEMAS = "https://raw.githubusercontent.com/yomidevs/yomitan/master/ext/data/schemas/"
SAMPLE_EVERY = 1000  # jsonschema is slow on nested structured content; every entry would take hours
MAX_HEADWORD_LEN = 16


def schema(name):
    with urllib.request.urlopen(SCHEMAS + name, timeout=60) as r:
        return json.load(r)


def main():
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "naer_terms.zip"
    term_schema = schema("dictionary-term-bank-v3-schema.json")
    index_schema = schema("dictionary-index-schema.json")
    item = jsonschema.Draft7Validator({**term_schema["items"], "definitions": term_schema.get("definitions", {})})

    errors = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        index = json.loads(z.read("index.json"))
        jsonschema.validate(index, index_schema)
        repo_index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
        if index != repo_index:
            errors.append("packaged index.json differs from the repo's index.json")
        for key in ("isUpdatable", "indexUrl", "downloadUrl", "revision"):
            if not index.get(key):
                errors.append(f"index.json has no {key}")
        for required in ("styles.css", "source_attribution.txt"):
            if required not in names:
                errors.append(f"missing {required}")
        banks = [n for n in names if n.startswith("term_bank_")]
        if not banks:
            errors.append("no term banks")
        n = checked = 0
        for name in banks:
            for entry in json.loads(z.read(name)):
                n += 1
                if len(entry[0]) > MAX_HEADWORD_LEN:
                    errors.append(f"{name}: headword longer than {MAX_HEADWORD_LEN}: {entry[0]}")
                if n % SAMPLE_EVERY == 1:
                    checked += 1
                    errors.extend(f"{name} {entry[0]}: {e.message[:200]}" for e in item.iter_errors(entry))
    print(f"{zip_path.name}: revision {index['revision']}, {len(banks)} term banks, {n:,} entries, "
          f"{checked:,} checked against the schema")
    if errors:
        print("\n".join(errors[:20]))
        sys.exit(f"{len(errors)} problems")
    print("ok")


if __name__ == "__main__":
    main()
