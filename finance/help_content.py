"""Strict local help resource loader. No network or Markdown rendering dependency."""
import re

HELP_TOPICS=('快速开始','账户与绑定','导入与分类','退款与口径','跨源对账','预算','储蓄','AI 助手','数据与隐私','备份恢复','常见问题')


def load_help(path):
    text=path.read_text(encoding='utf-8')
    matches=list(re.finditer(r'^## (.+)$',text,re.MULTILINE));chapters={}
    for i,match in enumerate(matches):
        title=match.group(1).strip()
        if title in chapters:raise ValueError('帮助章节重复')
        chapters[title]=text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip()
    if tuple(chapters)!=HELP_TOPICS or any(not content for content in chapters.values()):raise ValueError('帮助章节缺失、顺序错误或内容为空')
    return chapters
