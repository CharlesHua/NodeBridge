"""Validate and compile the Qt English catalog (run in the project environment)."""
import ast
from collections import Counter
from pathlib import Path
import string
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "nodebridge" / "assets" / "i18n" / "nodebridge_en.ts"


def placeholders(text):
    return Counter(field for _, field, _, _ in string.Formatter().parse(text) if field is not None)


def validate():
    messages = {}
    for item in ET.parse(CATALOG).findall(".//message"):
        source = item.findtext("source")
        translated = item.findtext("translation")
        if not translated or item.find("translation").get("type") == "unfinished":
            raise ValueError(f"Missing translation: {source}")
        if source in messages:
            raise ValueError(f"Duplicate source: {source}")
        if placeholders(source) != placeholders(translated):
            raise ValueError(f"Translation placeholders differ: {source}")
        messages[source] = translated
    for path in (ROOT / "nodebridge").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                source = node.args[0].value
                if any("\u4e00" <= char <= "\u9fff" for char in source) and source not in messages:
                    raise ValueError(f"Uncatalogued text at {path.name}:{node.lineno}: {source}")
    return len(messages)


def main():
    count = validate()
    if "--check" not in sys.argv:
        import PySide6
        base = Path(PySide6.__file__).parent
        executable = base / ("lrelease.exe" if sys.platform == "win32" else "lrelease")
        subprocess.run([str(executable), str(CATALOG), "-qm", str(CATALOG.with_suffix(".qm"))], check=True)
    print(f"Validated {count} translations.")


if __name__ == "__main__":
    main()
