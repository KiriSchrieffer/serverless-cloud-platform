from enum import StrEnum


class InvocationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELED = "CANCELED"


class InvocationAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class WorkerStatus(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"
