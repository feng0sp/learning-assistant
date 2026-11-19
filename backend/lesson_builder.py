"""Compact authoring helpers for original, source-reviewed core lessons."""

from .models import StudyGuide


def make_lesson(prefix, number, slug, title, overview, points, example, analogy, quiz, refs):
    source_id = f'{prefix}-{slug}-note'
    guide = StudyGuide.model_validate({
        'overview': overview,
        'objectives': [f'能解释并应用：{p[0]}' for p in points],
        'prerequisites': ['会进行基本四则运算和二进制换算；建议按本课程顺序学习'],
        'knowledge_points': [dict(title=p[0], explanation=p[1], key_takeaway=p[2],
                                  pitfalls=[p[3]], source_ids=[source_id]) for p in points],
        'worked_examples': [dict(title='按步骤推导', question=example[0], steps=example[1],
                                  answer=example[2], mistake=example[3])],
        'analogies': [dict(concept=title, scene=analogy[0], mapping=analogy[1], limitation=analogy[2])],
        'quiz': [dict(question=q[0], options=q[1], answer=q[2], explanation=q[3]) for q in quiz],
    }).model_dump()
    return dict(id=f'{prefix}-v1-{slug}', unit=number - 1, number=number, title=title, minutes=25,
                guide=guide, sources=[dict(id=source_id, label='知章原创核心导学 · 非教材原文',
                text='\n\n'.join(p[0] + '：' + p[1] for p in points) + '\n\n核对与延伸阅读：\n' + '\n'.join(refs))])
