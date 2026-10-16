"""Create an uploadable source tree with an explicit file allowlist."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
files = ['desktop.py', 'app.py', 'README.md', 'DESKTOP_README.md', 'WINDOWS_RELEASE.md',
         'RELEASE_NOTES.md', 'requirements.txt', 'requirements-lock.txt',
         'requirements-desktop.txt', 'pytest.ini', '.gitignore', 'start_desktop.bat',
         'start.bat', 'start.ps1']
for directory, pattern in [('finance', '*.py'), ('tests', '*.py'), ('packaging', '*.py'),
                           ('packaging', '*.ps1'), ('packaging', '*.iss'), ('samples', '*.csv'),
                           ('.github/workflows', '*.yml'), ('assets', '*.ico'), ('assets', '*.png'), ('assets', '*.txt')]:
    files.extend(str(p.relative_to(root)) for p in (root / directory).glob(pattern))
(root / 'release').mkdir(exist_ok=True)
with ZipFile(root / 'release' / 'AIFinanceHelper-source.zip', 'w', ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(root / name, name)
print('Source archive created without user databases or credentials.')
