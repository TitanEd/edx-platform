"""
Instructor tasks related to enrollments.
"""

import logging
from datetime import datetime
from time import time
from pytz import UTC
from lms.djangoapps.instructor_analytics.basic import enrolled_students_features, list_may_enroll
from lms.djangoapps.instructor_analytics.csvs import format_dictlist
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.instructor_task.tasks_helper.grades import get_excluded_role_user_ids

from .runner import TaskProgress
from .utils import upload_csv_to_report_store

TASK_LOG = logging.getLogger('edx.celery.task')
FILTERED_OUT_ROLES = ['staff', 'instructor', 'finance_admin', 'sales_admin']


def upload_may_enroll_csv(_xblock_instance_args, _entry_id, course_id, task_input, action_name):
    """
    For a given `course_id`, generate a CSV file containing
    information about students who may enroll but have not done so
    yet, and store using a `ReportStore`.
    """
    start_time = time()
    start_date = datetime.now(UTC)
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    current_step = {'step': 'Calculating info about students who may enroll'}
    task_progress.update_task_state(extra_meta=current_step)

    # Compute result table and format it
    query_features = task_input.get('features')
    student_data = list_may_enroll(course_id, query_features)
    header, rows = format_dictlist(student_data, query_features)

    task_progress.attempted = task_progress.succeeded = len(rows)
    task_progress.skipped = task_progress.total - task_progress.attempted

    rows.insert(0, header)

    current_step = {'step': 'Uploading CSV'}
    task_progress.update_task_state(extra_meta=current_step)

    # Perform the upload
    upload_csv_to_report_store(rows, 'may_enroll_info', course_id, start_date)

    return task_progress.update_task_state(extra_meta=current_step)


def upload_students_csv(_xblock_instance_args, _entry_id, course_id, task_input, action_name):
    """
    For a given `course_id`, generate a CSV file containing profile
    information for all students that are enrolled, excluding staff and instructors,
    and store using a `ReportStore`.
    """
    start_time = time()
    start_date = datetime.now(UTC)

    # Exclude staff and instructors
    excluded_ids = get_excluded_role_user_ids(course_id)
    TASK_LOG.debug(f"Excluded staff/instructor user IDs: {excluded_ids}")

    # Query enrolled students, excluding staff/instructors
    enrolled_students = CourseEnrollment.objects.users_enrolled_in(course_id).exclude(id__in=excluded_ids)
    TASK_LOG.debug(f"Students included in report (pre-features): {[student.username for student in enrolled_students]}")

    task_progress = TaskProgress(action_name, enrolled_students.count(), start_time)

    current_step = {'step': 'Calculating Profile Info'}
    task_progress.update_task_state(extra_meta=current_step)

    # Compute the student features table
    query_features = task_input.get('features')
    student_data = enrolled_students_features(course_id, query_features)

    # Filter out staff/instructor users from student_data
    filtered_student_data = [
        data for data in student_data
        if int(data.get('id')) not in excluded_ids
    ]
    TASK_LOG.debug(f"Filtered student data IDs: {[data.get('id') for data in filtered_student_data]}")

    # Format the filtered data
    header, rows = format_dictlist(filtered_student_data, query_features)

    task_progress.attempted = task_progress.succeeded = len(rows)
    task_progress.skipped = task_progress.total - task_progress.attempted

    rows.insert(0, header)

    current_step = {'step': 'Uploading CSV'}
    task_progress.update_task_state(extra_meta=current_step)

    # Perform the upload
    upload_parent_dir = task_input.get('upload_parent_dir', '')
    upload_filename = task_input.get('filename', 'student_profile_info')
    upload_csv_to_report_store(rows, upload_filename, course_id, start_date, parent_dir=upload_parent_dir)

    return task_progress.update_task_state(extra_meta=current_step)