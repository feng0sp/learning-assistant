"""Render a complete chapter for browser printing; all learning text is escaped."""
from html import escape
from urllib.parse import quote


def text(value):
    return escape(str(value), quote=True)


def paragraph(value):
    return '<p class="text">' + text(value) + '</p>'


def items(values, ordered=False):
    tag = 'ol' if ordered else 'ul'
    return '<' + tag + '>' + ''.join('<li class="text">' + text(value) + '</li>' for value in values) + '</' + tag + '>'


def render_handout(chapter):
    guide = chapter['guide']
    labels = {
        'demo': '内置教学示例，非教材原文',
        'material': '根据用户资料整理；例题、自测和类比为教学原创',
        'topic': '按标题生成的通用讲解，未读取教材原文',
    }
    result = ['<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>' + text(chapter['title']) + ' · 学习讲义</title>',
              '<link rel="stylesheet" href="/handout.css"><script defer src="/handout.js"></script></head><body>',
              '<aside class="print-controls" aria-label="打印设置"><div><strong>学习讲义</strong>',
              '<a href="/#chapter/' + quote(chapter['id'], safe='') + '">返回章节</a></div>',
              '<div class="print-options"><label><input id="include-solutions" type="checkbox" checked> 包含答案与例题解析</label>',
              '<label><input id="include-notes" type="checkbox" checked> 包含我的笔记</label>',
              '<label><input id="include-sources" type="checkbox"> 附上来源原文</label></div>',
              '<p>内容取自打开讲义时已保存的章节。可在浏览器打印窗口选择「另存为 PDF」。建议使用 A4，关闭浏览器自带的页眉和页脚。</p>',
              '<button id="print-handout" type="button">打印 / 保存为 PDF</button><span id="print-status" role="status"></span></aside>',
              '<main><header class="handout-heading"><p class="brand">知章 · 个人学习讲义</p>',
              '<h1>' + text(chapter['title']) + '</h1>',
              paragraph('课程：' + chapter['course'] + '\n教材：' + (chapter['textbook'] or '未指定教材')),
              '<p class="provenance">' + text(labels[chapter['mode']]) + '</p></header>',
              '<section><h2>章节概览</h2>' + paragraph(guide['overview']),
              '<h3>学习目标</h3>' + items(guide['objectives'])]
    if guide['prerequisites']:
        result += ['<h3>先备知识</h3>', items(guide['prerequisites'])]
    result += ['</section><section><h2>核心知识点</h2>']
    source_labels = {source['id']: source['label'] for source in chapter['sources']}
    for index, point in enumerate(guide['knowledge_points'], 1):
        result += ['<article><h3>' + text(f'{index}. {point["title"]}') + '</h3>',
                   paragraph(point['explanation']), '<p class="takeaway"><strong>记住：</strong>' + text(point['key_takeaway']) + '</p>']
        if point['pitfalls']:
            result += ['<h4>易错点</h4>', items(point['pitfalls'])]
        if point['source_ids']:
            result.append('<p class="citation">依据：' + text('；'.join('[' + source_id + '] ' + source_labels[source_id] for source_id in point['source_ids'])) + '</p>')
        result.append('</article>')
    result += ['</section><section><h2>原创例题</h2>']
    for index, example in enumerate(guide['worked_examples'], 1):
        result += ['<article><h3>' + text(f'例题 {index} · {example["title"]}') + '</h3>', paragraph(example['question']),
                   '<div class="solution">' + items(example['steps'], ordered=True),
                   '<p class="answer"><strong>答案：</strong>' + text(example['answer']) + '</p>']
        if example['mistake']:
            result.append(paragraph('易错提醒：' + example['mistake']))
        result += ['</div><div class="answer-space" aria-hidden="true"></div></article>']
    result += ['</section><section><h2>生活类比</h2>']
    for analogy in guide['analogies']:
        result += ['<article><h3>' + text(analogy['concept']) + '</h3>', paragraph(analogy['scene']), items(analogy['mapping']),
                   '<p class="limitation"><strong>类比的局限：</strong>' + text(analogy['limitation']) + '</p></article>']
    result += ['</section><section class="personal-notes"><h2>我的理解与笔记</h2>',
               paragraph(chapter['notes'] or '尚未添加笔记。'), '</section>',
               '<section class="quiz-section"><h2>自测练习</h2><p>先独立作答，再核对解析。本讲义不显示此前的作答结果。</p>']
    for index, question in enumerate(guide['quiz'], 1):
        result += ['<article class="quiz-question"><h3>' + text(f'{index}. {question["question"]}') + '</h3>',
                   '<ul class="options">' + ''.join('<li>' + text(chr(65 + choice) + '. ' + option) + '</li>' for choice, option in enumerate(question['options'])) + '</ul>',
                   '<p class="write-answer">我的答案：________　理由：________________________________</p></article>']
    result += ['</section><section class="answer-key solution"><h2>自测答案与解析</h2><p>完成练习后再翻到这一页。</p>']
    for index, question in enumerate(guide['quiz'], 1):
        result += ['<article><h3>' + text(f'{index}. 正确答案：{chr(65 + question["answer"])}') + '</h3>', paragraph(question['explanation']), '</article>']
    result += ['</section><section class="source-appendix"><h2>资料来源原文</h2>']
    if not chapter['sources']:
        result.append(paragraph('本章未提供教材原文。'))
    for source in chapter['sources']:
        result += ['<article><h3>' + text('[' + source['id'] + '] ' + source['label']) + '</h3>', paragraph(source['text']), '</article>']
    result += ['</section><footer>知章 · 理解后再记忆，完成后再核对。生成内容仍需与教材核对。</footer></main></body></html>']
    return ''.join(result)
