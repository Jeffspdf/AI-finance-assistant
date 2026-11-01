# Windows build and release

Requirements: Windows x64, Python 3.12 with Tk, Inno Setup 6.

```powershell
python -m pip install -r requirements-desktop.txt
.\packaging\build.ps1 -Python python -Version '0.7.0' -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Outputs in release/: Setup EXE, portable ZIP, SHA256SUMS.txt. The source archive is an explicit allowlist of code, public documentation, generated assets and synthetic test evidence; no user DB, Key, venv or logs. The Windows AppId and legacy private data directory remain stable for upgrades.

The repository is https://github.com/Jeffspdf/AI-finance-assistant . A `vX.Y.Z` tag runs the Windows release workflow, smoke-tests the packaged app and creates a draft if no release exists. Review checksums/notes before publishing. Every push/PR runs the separate test workflow. A locally passing test does not prove remote CI success.

In-app updates query public GitHub formal releases. Downloads compare against the release asset digest or official SHA256SUMS manifest. A mismatch is rejected; missing verification data prevents direct download and leaves the browser option. Hashes are not publisher signatures. Users quit the app and run the installer manually. No automatic code execution or third-party mirrors.

Release audit: verify official non-draft/non-prerelease tag, exact installer filename and byte size/digest. A local artifact is not considered published until the API confirms it. No clean VM or certificate is claimed. Backup data by exiting and copying the data directory. The uninstaller keeps user data.
