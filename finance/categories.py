import re

CATEGORIES = ['餐饮', '交通', '购物', '居住', '生活缴费', '教育', '医疗', '娱乐', '工资', '生活费', '其他收入', '转账', '其他支出']
RULES = {
    '餐饮': ['餐', '饭', '食堂', '咖啡', '奶茶', '外卖', '美团', '饿了么', '面馆', '肯德基', '麦当劳', '星巴克', '便利店'],
    '交通': ['地铁', '公交', '打车', '滴滴', '铁路', '火车', '高铁', '机票', '加油', '停车', '共享单车'],
    '居住': ['房租', '租金', '公寓', '物业'],
    '生活缴费': ['水费', '电费', '燃气', '话费', '宽带', '通信'],
    '教育': ['学费', '课程', '教材', '图书', '书店', '培训', '考试'],
    '医疗': ['医院', '药店', '药房', '挂号', '诊所'],
    '娱乐': ['电影', '影院', '游戏', '视频会员', '音乐会员', '演唱会', '健身'],
    '购物': ['淘宝', '京东', '拼多多', '超市', '服装', '鞋', '数码', '购物'],
}

def classify(merchant: str, description: str, kind: str):
    text = f'{merchant} {description}'.lower()
    if kind == '转账':
        return '转账', '规则'
    if kind == '收入':
        if any(w in text for w in ['工资', '薪资', 'salary', '奖金']):
            return '工资', '规则'
        if any(w in text for w in ['生活费', '零花钱', '家人']):
            return '生活费', '规则'
        return '其他收入', '待确认'
    for category, words in RULES.items():
        if any(w in text for w in words):
            return category, '规则'
    return '其他支出', '待确认'

def category_in_question(text):
    for category in CATEGORIES:
        if category in text:
            return category
    for alias in ['吃饭', '饮食', '伙食']:
        if alias in text:
            return '餐饮'
    return None
