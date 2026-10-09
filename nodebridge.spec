# Windows one-folder portable build. Use scripts/build_portable.py so each build has a fresh output folder.
from pathlib import Path
import sys

project = Path(SPECPATH)
assets = project / "nodebridge" / "assets"
conda_dlls = Path(sys.prefix) / "Library" / "bin"
runtime_dlls = [
    (str(conda_dlls / name), ".")
    for name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll", "libexpat.dll", "ffi-8.dll")
    if (conda_dlls / name).exists()
]

a = Analysis(
    [str(project / "nodebridge" / "__main__.py")],
    pathex=[str(project)],
    binaries=runtime_dlls,
    datas=[(str(assets), "nodebridge/assets"), (str(project / "docs" / "USER_GUIDE.md"), "docs"),
           (str(project / "LICENSE"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NodeBridge",
    console=False,
    icon=str(assets / "nodebridge.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NodeBridge",
)
