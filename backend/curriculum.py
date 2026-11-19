"""Canonical rewardable courses; legacy question positions remain unchanged."""
from functools import lru_cache
from . import course_registry


@lru_cache(maxsize=1)
def all_lessons():
    return course_registry.all_lessons()


def course_id(chapter_id):
    return course_registry.course_id(chapter_id)


def reward_capacity():
    return sum(1 + len(item['guide']['quiz']) for item in all_lessons().values())
