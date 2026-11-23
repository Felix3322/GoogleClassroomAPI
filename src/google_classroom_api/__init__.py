"""GoogleClassroomAPI — run Google Classroom in a teacher's terms, with cookies.

The public Classroom API (:class:`Classroom`) drives the same private endpoints
as the Classroom web app, authenticated by the teacher's exported browser
cookies. No OAuth client, no Google Cloud project, no browser at run time::

    from google_classroom_api import Classroom

    cls = Classroom.from_cookie_file("cookies.txt")
    course = Classroom.course_id_from_url("https://classroom.google.com/u/0/c/MTIzNDU2Nzg5MDEy")

    cls.course(course)                 # {course_id, name, section}
    cls.students(course)               # [{user_id, name, email, photo_url}]
    a = cls.create_assignment(course, "Lesson 12", instructions="...", topic="Week 1",
                              form_url="https://docs.google.com/forms/d/e/.../viewform")
    cls.assignments(course)
    cls.set_missing_work_grade(course, enabled=True, percent=50)
    cls.delete_assignment(course, a["item_id"])

Google Forms is not covered yet: Google binds the Docs/Forms editing session to
the device, so exported cookies cannot drive the form editor outside a browser.
The official-API Forms code in this package (``forms``, ``formbuilder``,
``oauth_backends``) is kept for that later work.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from .auth import DEFAULT_SCOPES as SCOPES
from .auth import OAuthClient, TokenManager, TokenSet
from .backends import (AssignmentResult, ClassroomBackend, FormsBackend, FormSync, ResolvedSlot,
                       ResponseData, ResponseMeta, StudentInfo, SubmissionInfo)
from .classroom import ClassroomApi
from .cookie_classroom import UNCHANGED, Classroom, course_id_from_url
from .drive import DriveApi
from .errors import (AccessMismatch, ClassroomAPIError, GoogleApiError, NotLinked, ReauthRequired,
                     RecipientError, UncertainOutcome)
from .forms import FormsApi
from .models import Choice, Lesson, Question, Section
from .oauth_backends import OAuthClassroomBackend, OAuthFormsBackend
from .pipeline import ALL_STUDENTS, Pipeline, PublishResult, judge
from .store import Store
from .transport import GoogleSession
from .web.classroom import StreamItemRejected
from .web.session import WebProtocolError, WebSession

__all__ = [
    "Classroom", "UNCHANGED", "course_id_from_url",
    "WebSession", "WebProtocolError", "StreamItemRejected",
    "Choice", "Lesson", "Question", "Section",
    "Store", "Pipeline", "PublishResult", "ALL_STUDENTS", "judge",
    "FormsBackend", "ClassroomBackend", "FormSync", "ResolvedSlot", "ResponseData", "ResponseMeta",
    "StudentInfo", "SubmissionInfo", "AssignmentResult",
    "OAuthFormsBackend", "OAuthClassroomBackend", "FormsApi", "ClassroomApi", "DriveApi",
    "GoogleSession", "OAuthClient", "TokenManager", "TokenSet",
    "ClassroomAPIError", "ReauthRequired", "GoogleApiError", "RecipientError", "AccessMismatch",
    "NotLinked", "UncertainOutcome",
    "authorize", "connect_oauth", "SCOPES",
]


def authorize(client_secrets_file: str, *, scopes: Optional[list] = None, port: int = 0,
              open_browser: bool = True) -> dict:
    """Run the one-time OAuth consent and return a token dict for ``connect_oauth``.

    Needs a browser on this machine and the ``oauth`` extra
    (``pip install GoogleClassroomAPI[oauth]``). ``client_secrets_file`` is
    the OAuth client JSON from Google Cloud (type: Desktop app). Persist the
    returned dict; it holds the refresh token that makes every later run headless.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "authorize needs the 'oauth' extra: pip install GoogleClassroomAPI[oauth]") from exc
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, scopes or SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser)
    return {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token": creds.token,
        "expires_at": creds.expiry.timestamp() if creds.expiry else 0.0,
    }


def connect_oauth(
    token_info: Optional[Mapping[str, Any]] = None,
    store: Optional[Store] = None,
    *,
    teacher_id: str = "default",
    on_token_update: Optional[Callable[[dict], None]] = None,
) -> Pipeline:
    """Build a :class:`Pipeline` on the official OAuth APIs (forms + classroom).

    ``token_info`` is what :func:`authorize` returned (or a stored copy). If it
    is omitted, the token is loaded from the ``store`` under ``teacher_id``.
    Refreshed tokens are written back to the store by default; pass
    ``on_token_update`` to persist them elsewhere.
    """
    store = store or Store()
    if token_info is None:
        token_info = store.get_tokens(teacher_id)
        if token_info is None:
            raise ClassroomAPIError(f"no stored token for teacher_id {teacher_id!r}; call authorize() first")

    oauth = OAuthClient(token_info["client_id"], token_info["client_secret"])
    tokens = TokenSet(
        access_token=token_info.get("token") or token_info.get("access_token") or "",
        refresh_token=token_info["refresh_token"],
        expires_at=float(token_info.get("expires_at") or 0.0),
        scope=token_info.get("scope", ""),
    )

    def _save(new: TokenSet) -> None:
        record = {
            "client_id": token_info["client_id"],
            "client_secret": token_info["client_secret"],
            "refresh_token": new.refresh_token,
            "token": new.access_token,
            "expires_at": new.expires_at,
            "scope": new.scope,
        }
        (on_token_update or (lambda r: store.save_tokens(teacher_id, r)))(record)

    manager = TokenManager(oauth, tokens, on_update=_save)
    session = GoogleSession(manager)
    forms = OAuthFormsBackend(FormsApi(session), DriveApi(session))
    classroom = OAuthClassroomBackend(ClassroomApi(session))
    return Pipeline(forms, classroom, store)
