# PyInstaller specification for the native Python desktop sidecar.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_root = Path(SPEC).resolve().parents[1]

datas = []
binaries = []
hiddenimports = []

for package in ("ctranslate2", "faster_whisper", "onnxruntime"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

analysis = Analysis(
    [str(project_root / "src" / "wecanfindintern" / "desktop" / "server.py")],
    pathex=[str(project_root), str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
# The upstream PyInstaller hook bundles ~100 MB of discovery documents for
# hundreds of unrelated Google APIs. This app's Gemini file client fetches its
# own v1beta discovery document and does not read that cache.
analysis.datas = [
    entry
    for entry in analysis.datas
    if not entry[0].startswith("googleapiclient/discovery_cache/documents/")
]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="wecanfindintern-backend",
    console=True,
)
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="wecanfindintern-backend",
)
