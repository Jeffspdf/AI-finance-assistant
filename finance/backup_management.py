"""Explicit management of snapshots belonging to one ledger; never auto-delete."""
from datetime import datetime,timezone
from pathlib import Path
import json,re,uuid,sqlite3,stat
from .migrations import inspect_database,SCHEMA_VERSION


class BackupManagement:
    @staticmethod
    def _backup_identity(info):
        return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

    def _checked_backup_stat(self,path):
        folder=self.managed_backup_folder()
        info=path.lstat()
        if (path.parent!=folder or not stat.S_ISREG(info.st_mode) or
            getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400) or
            path.resolve().parent!=folder):
            raise ValueError('备份文件或路径已变化，请刷新列表后重新确认；未删除文件')
        return info

    def managed_backup_folder(self):
        folder=Path(self.path).resolve().parent/'backups'
        if folder.resolve()!=folder:raise ValueError('备份目录是重定向路径，请在文件管理器中核对；未读取或清理文件')
        return folder

    def create_managed_backup(self, purpose):
        if self.read_only:raise ValueError('只读账本不能创建备份')
        if purpose not in ('manual', 'restore', 'semantic'):raise ValueError('未知备份用途')
        folder=self.managed_backup_folder()
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        return self.backup(folder/f'{Path(self.path).stem}-{purpose}-v{SCHEMA_VERSION}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3')

    def create_semantic_backup(self):
        return self.create_managed_backup('semantic')

    def managed_backups(self):
        folder=self.managed_backup_folder()
        pattern=re.compile(re.escape(Path(self.path).stem)+r'-(?:(manual|restore|semantic)-)?v\d+-(\d{8}T\d{6})-([0-9a-f]{8})\.sqlite3')
        purposes={'manual':'手动备份','restore':'恢复前自动备份','semantic':'语义修复前'}
        rows=[]
        if not folder.is_dir():return rows
        for path in folder.iterdir():
            match=pattern.fullmatch(path.name)
            if not match:continue
            try:
                info=self._checked_backup_stat(path)
                created=datetime.strptime(match[2],'%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
            except (ValueError,OSError):continue
            rows.append(dict(path=str(path),name=path.name,bytes=info.st_size,modified=info.st_mtime,identity=self._backup_identity(info),
                             created=created.timestamp(), reference=match[3],
                             purpose=purposes.get(match[1],'旧版/通用快照（用途未记录）')))
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

    def delete_managed_backup(self,path,*,confirmed=False,expected_identity=None):
        if self.read_only:raise ValueError('只读账本不能清理备份')
        if confirmed is not True:raise ValueError('请先确认删除此修复前快照；删除后无法用它回滚')
        with self._lock:
            row=self._managed_backup(path)
            if expected_identity is not None and tuple(expected_identity)!=tuple(row['identity']):
                raise ValueError('选中快照在确认期间已变化，请刷新列表后重新确认；未删除文件')
            target=Path(row['path']);current=self._checked_backup_stat(target)
            if self._backup_identity(current)!=tuple(row['identity']):
                raise ValueError('备份文件在删除前已变化，请刷新列表后重新确认；未删除文件')
            target.unlink()
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
