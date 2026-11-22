"""Google Drive API v3 (official): files, and who can open them.

The same permission model covers two things here:

* ordinary readers of a file (a PDF, a Google Doc), and
* responders of a Google Form, which are ``reader`` permissions with
  ``view = "published"``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from .transport import GoogleSession

BASE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

FORM_MIME = "application/vnd.google-apps.form"
_PERMISSION_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,view)"
_FILE_FIELDS = "id,name,mimeType,webViewLink,appProperties,copyRequiresWriterPermission,createdTime"


@dataclass
class AccessReport:
    """What was asked for, what was changed, and what Google reports afterwards."""

    file_id: str
    published_view: bool
    expected: list[str]
    actual: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    broad: list[str] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        """True when exactly the expected people have access and nobody else."""
        return not self.unexpected and not self.missing and not self.broad

    @property
    def covers_expected(self) -> bool:
        """True when everyone expected has access and there is no group-wide access."""
        return not self.missing and not self.broad

    def to_dict(self) -> dict:
        data = asdict(self)
        data["matches"] = self.matches
        return data


def _is_reader(permission: dict, published_view: bool) -> bool:
    if published_view:
        return permission.get("view") == "published"
    return not permission.get("view") and permission.get("role") in ("reader", "commenter")


def _label(permission: dict) -> str:
    kind = permission.get("type", "?")
    return f"{kind}:{permission.get('emailAddress') or permission.get('domain') or '*'}"


class DriveApi:
    def __init__(self, session: GoogleSession) -> None:
        self._s = session

    # --- files ----------------------------------------------------------------

    def upload(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        *,
        app_properties: Optional[dict] = None,
        block_download: bool = True,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Upload a file. ``block_download`` turns off download, print and copy for readers."""
        metadata: dict = {"name": name, "mimeType": mime_type}
        if app_properties:
            metadata["appProperties"] = app_properties
        if block_download:
            metadata["copyRequiresWriterPermission"] = True
        if parent_id:
            metadata["parents"] = [parent_id]
        boundary = "cfb" + uuid.uuid4().hex
        body = b"".join(
            [
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                json.dumps(metadata).encode("utf-8"),
                f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        return self._s.request(
            "POST",
            UPLOAD,
            params={"uploadType": "multipart", "fields": _FILE_FIELDS, "supportsAllDrives": "true"},
            content=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )

    def get_file(self, file_id: str) -> dict:
        return self._s.request(
            "GET",
            f"{BASE}/files/{file_id}",
            params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"},
        )

    def update_file(self, file_id: str, fields: dict) -> dict:
        return self._s.request(
            "PATCH",
            f"{BASE}/files/{file_id}",
            params={"fields": _FILE_FIELDS, "supportsAllDrives": "true"},
            json=fields,
        )

    def list_files(self, query: str) -> list[dict]:
        params = {"q": query, "fields": f"nextPageToken,files({_FILE_FIELDS})", "pageSize": 200}
        return list(self._s.paginate(f"{BASE}/files", "files", params=params))

    def find_by_app_property(self, key: str, value: str) -> list[dict]:
        """Find files this app tagged earlier. Used to recover after a timed-out upload."""
        escaped_key = key.replace("\\", "\\\\").replace("'", "\\'")
        escaped_value = value.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"appProperties has {{ key='{escaped_key}' and value='{escaped_value}' }} "
            "and trashed = false"
        )
        return self.list_files(query)

    def list_app_forms(self) -> list[dict]:
        """Forms visible to this app (with the ``drive.file`` scope: the ones it created)."""
        return self.list_files(f"mimeType = '{FORM_MIME}' and trashed = false")

    # --- permissions ----------------------------------------------------------

    def list_permissions(self, file_id: str, *, published_view: bool = False) -> list[dict]:
        params = {"fields": _PERMISSION_FIELDS, "supportsAllDrives": "true", "pageSize": 100}
        if published_view:
            params["includePermissionsForView"] = "published"
        url = f"{BASE}/files/{file_id}/permissions"
        return list(self._s.paginate(url, "permissions", params=params))

    def add_user(self, file_id: str, email: str, *, published_view: bool = False) -> dict:
        body = {"type": "user", "role": "reader", "emailAddress": email}
        if published_view:
            body["view"] = "published"
        return self._s.request(
            "POST",
            f"{BASE}/files/{file_id}/permissions",
            params={"sendNotificationEmail": "false", "supportsAllDrives": "true"},
            json=body,
            idempotent=True,
        )

    def remove_permission(self, file_id: str, permission_id: str) -> None:
        self._s.request(
            "DELETE",
            f"{BASE}/files/{file_id}/permissions/{permission_id}",
            params={"supportsAllDrives": "true"},
        )

    def reconcile_users(
        self,
        file_id: str,
        expected_emails: Iterable[str],
        *,
        published_view: bool = False,
        remove_unexpected: bool = True,
    ) -> AccessReport:
        """Make the reader list equal ``expected_emails``, then read it back.

        Owners and editors are never touched. Domain-wide, group and
        anyone-with-the-link access counts as ``broad`` and is removed when
        ``remove_unexpected`` is true.
        """
        expected = sorted({email.strip().lower() for email in expected_emails if email})
        report = AccessReport(file_id=file_id, published_view=published_view, expected=expected)

        current = [p for p in self.list_permissions(file_id, published_view=published_view)
                   if _is_reader(p, published_view)]
        by_email = {
            p["emailAddress"].lower(): p
            for p in current
            if p.get("type") == "user" and p.get("emailAddress")
        }
        for email in expected:
            if email not in by_email:
                self.add_user(file_id, email, published_view=published_view)
                report.added.append(email)
        if remove_unexpected:
            for permission in current:
                is_expected_user = (
                    permission.get("type") == "user"
                    and (permission.get("emailAddress") or "").lower() in expected
                )
                if not is_expected_user:
                    self.remove_permission(file_id, permission["id"])
                    report.removed.append(_label(permission))

        final = [p for p in self.list_permissions(file_id, published_view=published_view)
                 if _is_reader(p, published_view)]
        for permission in final:
            email = (permission.get("emailAddress") or "").lower()
            if permission.get("type") == "user" and email:
                report.actual.append(email)
                if email not in expected:
                    report.unexpected.append(email)
            else:
                report.broad.append(_label(permission))
        report.actual.sort()
        report.missing = [email for email in expected if email not in report.actual]
        return report
