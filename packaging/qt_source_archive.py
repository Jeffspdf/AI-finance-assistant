"""Package the working source used for the Qt build, including local edits."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {
    'assets': {'.md','.txt','.png','.ico'}, 'finance': {'.py'}, 'qt_finance': {'.py'},
    'tests': {'.py','.sql','.csv','.json','.xlsx'}, 'packaging': {'.ps1','.py','.iss'},
    'docs': {'.md','.json','.png','.csv','.xlsx','.xml','.txt','.diff'}, 'scripts': {'.py','.ps1'},
    'samples': {'.csv','.json','.xlsx','.md'}, '.github': {'.yml','.yaml'},
}


def main():
    from sys import argv
    version = argv[1]
    paths = [p for p in ROOT.iterdir() if p.is_file() and
             (p.suffix in ('.md','.py','.bat','.ps1') or p.name.startswith('requirements') and p.suffix=='.txt'
              or p.name in ('pytest.ini','.gitignore'))]
    for folder, extensions in DIRECTORIES.items():
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix.lower() in extensions
                     and p.name.lower() not in ('api.txt','model_settings.json') and '__pycache__' not in p.parts)
    out = ROOT/'release'/f'AIFinanceHelperQt-{version}-Source.zip'
    manifest = {}
    with ZipFile(out,'w',ZIP_DEFLATED) as archive:
        for p in sorted(paths):
            name = p.relative_to(ROOT).as_posix()
            data = p.read_bytes()
            archive.writestr(name,data)
            manifest[name] = hashlib.sha256(data).hexdigest()
        archive.writestr('SOURCE_MANIFEST.json',json.dumps(manifest,indent=2))
    with ZipFile(out) as archive:
        assert archive.testzip() is None
        assert all(hashlib.sha256(archive.read(k)).hexdigest()==v for k,v in manifest.items())
    print(out)


if __name__ == '__main__':
    main()
