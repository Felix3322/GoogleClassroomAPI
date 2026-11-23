"""Reverse-engineered clients for the private web protocols of Google Forms and Classroom.

Note the asymmetry Google's device-bound sessions force on us: the Classroom
RPCs still accept exported cookies off-device (see :class:`WebClassroomBackend`),
but the Forms editor does not, so :class:`FormsWeb`/:class:`FormEditor` only work
inside a live browser session and are kept for experiments, not for the headless
pipeline. Use the OAuth forms backend for anything headless.
"""

from .backend import WebClassroomBackend
from .classroom import ClassroomWeb, StreamItemRejected
from .forms import FormEditor, FormsWeb
from .session import WebProtocolError, WebSession

__all__ = ["ClassroomWeb", "WebClassroomBackend", "FormEditor", "FormsWeb",
           "StreamItemRejected", "WebProtocolError", "WebSession"]
