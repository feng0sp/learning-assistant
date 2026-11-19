"""Single source for subjects, learning paths, canonical IDs and public content."""
from copy import deepcopy
from functools import lru_cache

from . import core_courses, microclass, network_course

SUBJECTS = [
    dict(id='network', title='计算机网络', short='计网', description='从分层通信到定位网络故障', lab_id='network'),
    dict(id='os', title='操作系统', short='操作系统', description='理解执行、并发、内存与文件', lab_id='scheduler'),
    dict(id='organization', title='计算机组成原理', short='计组', description='沿数据通路理解程序如何执行', lab_id='cache'),
    dict(id='data-structures', title='数据结构', short='数据结构', description='用结构与操作分析算法代价', lab_id='hash'),
]
PATHS = [
    dict(id='network', subject_id='network', title='计算机网络 · 完整学习路径',
         intro='24 课 · 72 道自测 · 本科通用体系',
         about='原创讲义、分步例题、类比与自测。两套综合卷使用课内题目，适合复习，不作为独立测评。',
         search_hint='例如：子网、TCP、DNS', lab_id='network'),
    dict(id='microclass', subject_id='network', title='微课堂前三章 · 学懂再练',
         intro='12 小节 · 120 道选择题与逐项解析',
         about='配套原创解析，尚未逐节核对视频字幕，不是视频逐字稿。每课资料列有参考来源。',
         search_hint='例如：CRC、编码、ARP', lab_id='network'),
    *[dict(id=key, subject_id=key, title=title + ' · 核心导学',
           intro='8 节核心导学 · 32 道自测 · 配套可验证实验',
           about='本科通用核心导学，每节有知识点、分步例题、易错点及自测；不绑定指定教材或考试大纲。',
           search_hint='搜索本课程的概念或课时', lab_id=lab)
      for key, title, lab in [('os', '操作系统', 'scheduler'), ('organization', '计算机组成原理', 'cache'),
                              ('data-structures', '数据结构', 'hash')]],
]


def lessons_for(course_id):
    if course_id == 'network':
        return network_course.validated_lessons()
    if course_id == 'microclass':
        return microclass.validated_lessons()
    return core_courses.validated_lessons(course_id)


@lru_cache(maxsize=1)
def all_lessons():
    result = {}
    for path in PATHS:
        items = lessons_for(path['id'])
        if result.keys() & items.keys():
            raise ValueError('不同课程存在重复课时 ID。')
        result.update(items)
    return result


@lru_cache(maxsize=1)
def lesson_courses():
    return {key: path['id'] for path in PATHS for key in lessons_for(path['id'])}


def course_id(chapter_id):
    return lesson_courses().get(chapter_id)


def metadata():
    courses = []
    for path in PATHS:
        items = lessons_for(path['id'])
        courses.append(dict(path, lesson_ids=list(items), lesson_count=len(items),
                            question_count=sum(len(i['guide']['quiz']) for i in items.values()),
                            exam_ids=['net-exam-v1-1', 'net-exam-v1-2'] if path['id'] == 'network' else []))
    return {'version': 1, 'subjects': deepcopy(SUBJECTS), 'courses': courses}


def catalog(course_id):
    if course_id == 'network':
        return network_course.catalog()
    if course_id == 'microclass':
        return microclass.catalog()
    return core_courses.catalog(course_id)


def open_lesson(course_id, lesson_id):
    if course_id == 'network':
        return network_course.open_lesson(lesson_id)
    if course_id == 'microclass':
        return microclass.open_lesson(lesson_id)
    return core_courses.open_lesson(course_id, lesson_id)


def public_bundle():
    bundle = metadata()
    for path in bundle['courses']:
        lessons = deepcopy(list(lessons_for(path['id']).values()))
        path['lessons'] = lessons
        path['units'] = (list(network_course.UNITS) if path['id'] == 'network' else
                         list(microclass.UNITS) if path['id'] == 'microclass' else [i['title'] for i in lessons])
    return bundle
