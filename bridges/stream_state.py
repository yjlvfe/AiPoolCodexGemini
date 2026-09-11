"""Small provider-neutral streaming lifecycle state machine."""
from enum import Enum

class StreamState(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"

class StreamLifecycle:
    def __init__(self):
        self.state = StreamState.NOT_STARTED
        self.events = 0

    def begin(self):
        if self.state is not StreamState.NOT_STARTED:
            raise RuntimeError("stream already started")
        self.state = StreamState.STARTED

    def observe(self):
        if self.state is not StreamState.STARTED:
            raise RuntimeError("stream event outside started state")
        self.events += 1

    def complete(self):
        if self.state is not StreamState.STARTED:
            raise RuntimeError("stream completion outside started state")
        self.state = StreamState.COMPLETED

    def interrupt(self):
        if self.state is StreamState.COMPLETED:
            raise RuntimeError("completed stream cannot be interrupted")
        self.state = StreamState.INTERRUPTED

    @property
    def can_failover(self):
        return self.state is StreamState.NOT_STARTED

    def as_dict(self):
        return {"stream_state": self.state.value, "stream_events": self.events,
                "can_failover": self.can_failover}
