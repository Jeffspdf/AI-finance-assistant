# Windows build and release

Requirements: Windows x64, Python 3.12 with Tk, Inno Setup 6.

```powershell
python -m pip install -r requirements-desktop.txt
.\packaging\build.ps1 -Python python -Version '0.8.4' -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Outputs in release/: Setup EXE, portable ZIP, SHA256SUMS.txt. The source archive is an explicit allowlist of code, public documentation, generated assets and synthetic test evidence; no user DB, Key, venv or logs. The Windows AppId and legacy private data directory remain stable for upgrades.

The repository is https://github.com/Jeffspdf/AI-finance-assistant . A `vX.Y.Z` tag runs the Windows release workflow, smoke-tests the packaged app and creates a draft if no release exists. Review checksums/notes before publishing. Every push/PR runs the separate test workflow. A locally passing test does not prove remote CI success.

In-app updates query public GitHub formal releases. Downloads compare against the release asset digest or official SHA256SUMS manifest. A mismatch is rejected; missing verification data prevents direct download and leaves the browser option. Hashes are not publisher signatures. Users quit the app and run the installer manually. No automatic code execution or third-party mirrors.

Release audit: verify official non-draft/non-prerelease tag, exact installer filename and byte size/digest. A local artifact is not considered published until the API confirms it. No clean VM or certificate is claimed. Schema 4 automatically snapshots before upgrading. Settings provides consistent per-space backups and previewed restoration; downgrading requires a version-matched pre-upgrade backup. Keys are separate from ledger backups. The uninstaller keeps user data.

Every release must contain the current Setup EXE, portable ZIP, allowlisted source ZIP and SHA256SUMS.txt. The build generates and verifies those exact files, not previous releases in the output directory. Packaged TLS/UI and six-row M1a self-tests must pass before installer generation. CI uses official CPython 3.12; managed local runtimes retain their required public TLS resource, validated as parseable PEM. No trust bypass or certificate-store change is performed.
