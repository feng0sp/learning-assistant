"""Canonical rewardable courses; legacy question positions remain unchanged."""
from functools import lru_cache
from . import network_course, microclass


@lru_cache(maxsize=1)
def all_lessons():
    return {**network_course.validated_lessons(), **microclass.validated_lessons()}


def course_id(chapter_id):
    return 'microclass' if chapter_id in microclass.validated_lessons() else 'network'


def reward_capacity():
    return sum(1 + len(item['guide']['quiz']) for item in all_lessons().values())
