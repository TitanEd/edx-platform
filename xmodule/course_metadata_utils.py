"""
Simple utility functions that operate on course metadata.

This is a place to put simple functions that operate on course metadata. It
allows us to share code between the CourseBlock and CourseOverview
classes, which both need these type of functions.
"""

from base64 import b32encode
from datetime import datetime, timedelta
from math import exp

import dateutil.parser
from pytz import utc

# Static default start date
DEFAULT_START_DATE = datetime(2030, 1, 1, tzinfo=utc)

"""
Default grading policy for a course run.
"""
DEFAULT_GRADING_POLICY = {
    "GRADER": [
        {
            "type": "Homework",
            "short_label": "HW",
            "min_count": 12,
            "drop_count": 2,
            "weight": 0.15,
        },
        {
            "type": "Lab",
            "min_count": 12,
            "drop_count": 2,
            "weight": 0.15,
        },
        {
            "type": "Midterm Exam",
            "short_label": "Midterm",
            "min_count": 1,
            "drop_count": 0,
            "weight": 0.3,
        },
        {
            "type": "Final Exam",
            "short_label": "Final",
            "min_count": 1,
            "drop_count": 0,
            "weight": 0.4,
        }
    ],
    "GRADE_CUTOFFS": {
        "Pass": 0.5,
    },
}

"""
Custom grading policy for a course run.
"""
CUSTOM_GRADING_POLICY = {
    "GRADER": [
        {
            "type": "Written Work",
            "short_label": "work",
            "min_count": 12,
            "drop_count": 2,
            "weight": 0.30,
        },
        {
            "type": "Performance Task",
            "min_count": 12,
            "drop_count": 2,
            "weight": 0.40,
        },
        {
            "type": "Quarterly Assessment",
            "short_label": "Midterm",
            "min_count": 1,
            "drop_count": 0,
            "weight": 0.30,
        },
    ],
    "GRADE_CUTOFFS": {
        "Pass": 0.5,
    },
}

def get_dynamic_start_date():
    """
    Returns the dynamic start date, deferring to custom_extensions.waffle for Waffle switch logic.
    """
    try:
        from custom_extensions.waffle import get_default_start_date
        return get_default_start_date()
    except ImportError:
        return DEFAULT_START_DATE

def get_dynamic_grading_policy():
    """
    Returns the dynamic grading policy, deferring to custom_extensions.waffle for Waffle switch logic.
    """
    try:
        from custom_extensions.waffle import get_grading_policy
        return get_grading_policy()
    except ImportError:
        return DEFAULT_GRADING_POLICY

def clean_course_key(course_key, padding_char):
    """
    Encode a course's key into a unique, deterministic base32-encoded ID for
    the course.
    """
    encoded = b32encode(str(course_key).encode('utf8')).decode('utf8')
    return "course_{}".format(
        encoded.replace('=', padding_char)
    )

def number_for_course_location(location):
    """
    Given a course's block usage locator, returns the course's number.
    """
    return location.course

def has_course_started(start_date):
    """
    Given a course's start datetime, returns whether the current time's past it.
    """
    return datetime.now(utc) > start_date

def has_course_ended(end_date):
    """
    Given a course's end datetime, returns whether
        (a) it is not None, and
        (b) the current time is past it.
    """
    return datetime.now(utc) > end_date if end_date is not None else False

def is_enrollment_open(enrollment_start_date, enrollment_end_date):
    """
    Given a course's enrollment start and end datetime, returns if enrollment is open.
    """
    now = datetime.now(utc)
    enrollment_start_date = enrollment_start_date or datetime.min.replace(tzinfo=utc)
    enrollment_end_date = enrollment_end_date or datetime.max.replace(tzinfo=utc)
    return enrollment_start_date < now < enrollment_end_date

def course_starts_within(start_date, look_ahead_days):
    """
    Given a course's start datetime and look ahead days, returns True if
    course's start date falls within look ahead days otherwise False.
    """
    return datetime.now(utc) + timedelta(days=look_ahead_days) > start_date

def course_start_date_is_default(start, advertised_start):
    """
    Returns whether a course's start date hasn't yet been set.
    """
    return advertised_start is None and start == get_dynamic_start_date()

def sorting_score(start, advertised_start, announcement):
    """
    Returns a tuple that can be used to sort the courses according
    to how "new" they are.
    """
    announcement, start, now = sorting_dates(start, advertised_start, announcement)
    scale = 300.0  # about a year
    if announcement:
        days = (now - announcement).days
        score = -exp(-days / scale)
    else:
        days = (now - start).days
        score = exp(days / scale)
    return score

def sorting_dates(start, advertised_start, announcement):
    """
    Utility function to get datetime objects for dates used to
    compute the is_new flag and the sorting_score.
    """
    try:
        start = dateutil.parser.parse(advertised_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=utc)
    except (TypeError, ValueError, AttributeError):
        start = start
    now = datetime.now(utc)
    return announcement, start, now