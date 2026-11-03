"""Explicit management of snapshots belonging to one ledger; never auto-delete."""
from datetime import datetime,timezone
from pathlib import Path
import json,re,uuid,sqlite3
from .migrations import inspect_database,SCHEMA_VERSION


class BackupManagement:
    def managed_backup_folder(self):
        folder=Path(self.path).resolve().parent/'backups'
        if folder.resolve()!=folder:raise ValueError('备份目录是重定向路径，请在文件管理器中核对；未读取或清理文件')
        return folder

    def create_semantic_backup(self):
        if self.read_only:raise ValueError('只读账本不能创建修复备份')
        folder=self.managed_backup_folder()
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        return self.backup(folder/f'{Path(self.path).stem}-semantic-v{SCHEMA_VERSION}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3')

    def managed_backups(self):
        folder=self.managed_backup_folder()
        pattern=re.compile(re.escape(Path(self.path).stem)+r'-(?:semantic-)?v\d+-\d{8}T\d{6}-[0-9a-f]{8}\.sqlite3')
        rows=[]
        if not folder.is_dir():return rows
        for path in folder.iterdir():
            if not pattern.fullmatch(path.name) or path.is_symlink() or not path.is_file() or path.resolve().parent!=folder:continue
            stat=path.stat()
            rows.append(dict(path=str(path),name=path.name,bytes=stat.st_size,modified=stat.st_mtime,
                             purpose='语义修复前' if path.name.startswith(Path(self.path).stem+'-semantic-') else '旧版/通用快照（用途未记录）'))
        return sorted(rows,key=lambda r:(r['modified'],r['name']),reverse=True)

    def _managed_backup(self,path):
        requested=Path(path).absolute()
        for row in self.managed_backups():
            if Path(row['path'])==requested:return row
        raise ValueError('只能操作当前空间备份目录中列出的普通快照文件；未操作其他文件')

    def preview_managed_backup(self,path):
        row=self._managed_backup(path)
        try:return dict(row,**inspect_database(row['path']))
        except sqlite3.Error:raise ValueError('快照无法作为有效SQLite备份读取；未修改文件') from None

    def delete_managed_backup(self,path,*,confirmed=False):
        if self.read_only:raise ValueError('只读账本不能清理备份')
        if confirmed is not True:raise ValueError('请先确认删除此修复前快照；删除后无法用它回滚')
        row=self._managed_backup(path)
        with self._lock:Path(row['path']).unlink()
        return row['bytes']

    def semantic_repair_history(self):
        with self.connect(read_only=True) as db:
            rows=[dict(r) for r in db.execute("""SELECT a.entity_id,a.created_at,a.detail,COALESCE(t.merchant,'') merchant
                FROM audit_events a LEFT JOIN transactions t ON t.id=a.entity_id
                WHERE a.event='category_semantics_repaired' ORDER BY a.id DESC LIMIT 200""")]
        for row in rows:
            try:row['detail']=json.loads(row['detail'])
            except (ValueError,TypeError):row['detail']={}
        return rows
