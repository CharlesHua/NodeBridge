"""Build a clean one-folder Windows bundle and an archive without user data."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from shutil import copy2
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows edition on Windows.")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    bundle = ROOT / "dist" / "portable-builds" / run_id / "NodeBridge"
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", str(bundle.parent), str(ROOT / "nodebridge.spec")],
        cwd=ROOT,
        check=True,
    )
    if not (bundle / "NodeBridge.exe").is_file():
        raise SystemExit("The portable executable was not built.")
    copy2(ROOT / "LICENSE", bundle / "LICENSE")
    archive = ROOT / "dist" / "NodeBridge-portable.zip"
    pending = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in bundle.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(bundle)
            if relative.parts[0].lower() == "data":
                continue
            output.write(path, Path("NodeBridge") / relative)
    pending.replace(archive)
    print(f"Portable folder: {bundle}")
    print(f"Portable archive: {archive}")


if __name__ == "__main__":
    main()
