from pathlib import Path
from unittest.mock import patch
import pytest
from finance.help_content import load_help,HELP_TOPICS

ROOT=Path(__file__).resolve().parents[1]


def test_help_is_complete_offline_and_matches_current_actions():
    with patch('socket.socket',side_effect=AssertionError('Help must stay offline')):
        chapters=load_help(ROOT/'assets/help.md')
    assert tuple(chapters)==HELP_TOPICS
    text='\n'.join(chapters.values())
    for action in ('删除这笔交易','编辑选中账户','归档选中账户','恢复选中账户','导入账单','查看依据','备份与恢复'):assert action in text
    assert 'https://' not in text and 'C:\\Users' not in text


def test_help_explains_agent_authorization_and_deleted_import_choices():
    chapters=load_help(ROOT/'assets/help.md')
    agent=chapters['AI 储蓄规划']
    for term in ('A/B/C','历史模式','输入假设','工具轨迹','claims','本地规则规划','不会写账本','撤销授权'):
        assert term in agent
    assert '默认保持已删除状态' in chapters['导入与分类']
    assert '明确勾选' in chapters['导入与分类']


@pytest.mark.parametrize('fault',['missing','empty','duplicate'])
def test_incomplete_help_is_rejected(tmp_path,fault):
    text=(ROOT/'assets/help.md').read_text(encoding='utf-8')
    if fault=='missing':text=text.replace('## 快速开始','## 未知章节')
    elif fault=='empty':text=text[:text.index('## 常见问题')]+ '## 常见问题\n'
    else:text+='\n## 预算\n重复\n'
    path=tmp_path/'help.md';path.write_text(text,encoding='utf-8')
    with pytest.raises(ValueError):load_help(path)
