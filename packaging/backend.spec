# PyInstaller specification for the native Python desktop sidecar.

from pathlib import Path
import platform
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

project_root = Path(SPEC).resolve().parents[1]

datas = []
binaries = []
hiddenimports = [
    "scripts.collection.run_collection_campaign",
    *collect_submodules("jobspy"),
]

for package in ("ctranslate2", "faster_whisper", "onnxruntime"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# tls-client ships native libraries for every supported OS in one wheel. If
# PyInstaller inspects all of them, a macOS build tries to parse Linux ELF files
# as Mach-O (and vice versa). Bundle only the library used by this target.
tls_datas, tls_binaries, tls_hiddenimports = collect_all("tls_client")
machine = platform.machine().lower()
if sys.platform == "darwin":
    tls_native_name = "tls-client-arm64.dylib" if machine in {"arm64", "aarch64"} else "tls-client-x86.dylib"
elif sys.platform == "win32":
    tls_native_name = "tls-client-64.dll" if machine in {"amd64", "x86_64"} else "tls-client-32.dll"
else:
    tls_native_name = "tls-client-arm64.so" if machine in {"arm64", "aarch64"} else "tls-client-amd64.so"
datas += [entry for entry in tls_datas if not Path(entry[0]).name.startswith("tls-client-")]
binaries += [entry for entry in tls_binaries if Path(entry[0]).name == tls_native_name]
hiddenimports += [
    name
    for name in tls_hiddenimports
    if not name.startswith("tls_client.dependencies.tls-client-")
]

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
