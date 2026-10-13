"""Build a clean one-folder Windows bundle and an archive without user data."""

from __future__ import annotations

import os
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
    work_dir = ROOT / "build" / "portable-builds" / run_id
    log_path = ROOT / "dist" / "build_portable_latest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    print(f"Building {run_id}; UTF-8 build log: {log_path}", flush=True)
    with log_path.open("wb") as log:
        log.write(f"NodeBridge portable build {run_id}\n".encode("utf-8"))
        log.flush()
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
             "--workpath", str(work_dir),
             "--distpath", str(bundle.parent), str(ROOT / "nodebridge.spec")],
            cwd=ROOT, check=True, env=env,
            stdout=log, stderr=subprocess.STDOUT,
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
    with log_path.open("ab") as log:
        log.write(f"Portable folder: {bundle}\nPortable archive: {archive}\n".encode("utf-8"))
    print(f"Portable folder: {bundle}")
    print(f"Portable archive: {archive}")


if __name__ == "__main__":
    main()
