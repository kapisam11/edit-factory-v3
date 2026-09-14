import pytest

from ai_video_factory.job_state import (
    JobStatus,
    allowed_transitions,
    is_terminal,
    is_valid_transition,
    validate_transition,
)


def test_terminal_states_are_terminal():
    assert is_terminal(JobStatus.DONE.value)
    assert is_terminal(JobStatus.ERROR.value)
    assert is_terminal(JobStatus.CANCELLED.value)
    assert is_terminal(JobStatus.INTERRUPTED.value)
    assert not is_terminal(JobStatus.RUNNING.value)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        (JobStatus.RUNNING.value, JobStatus.DONE.value),
        (JobStatus.RUNNING.value, JobStatus.ERROR.value),
        (JobStatus.RUNNING.value, JobStatus.CANCELLING.value),
        (JobStatus.CANCELLING.value, JobStatus.CANCELLED.value),
    ],
)
def test_valid_transitions(current, target):
    assert is_valid_transition(current, target)
    validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.DONE.value, JobStatus.RUNNING.value),
        (JobStatus.ERROR.value, JobStatus.DONE.value),
        (JobStatus.CANCELLED.value, JobStatus.RUNNING.value),
        ("bogus", JobStatus.RUNNING.value),
    ],
)
def test_invalid_transitions(current, target):
    assert not is_valid_transition(current, target)
    with pytest.raises(ValueError):
        validate_transition(current, target)


def test_same_state_transition_is_idempotent():
    assert is_valid_transition(JobStatus.RUNNING.value, JobStatus.RUNNING.value)
    assert allowed_transitions(JobStatus.RUNNING.value)
