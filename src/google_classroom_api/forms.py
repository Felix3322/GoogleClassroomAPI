"""Google Forms API v1 (official)."""

from __future__ import annotations

from typing import Optional, Sequence

from .transport import GoogleSession

BASE = "https://forms.googleapis.com/v1"


class FormsApi:
    def __init__(self, session: GoogleSession) -> None:
        self._s = session

    def create(self, title: str, document_title: Optional[str] = None) -> dict:
        """Create an empty form. Google only accepts the titles here; items come later."""
        info = {"title": title}
        if document_title:
            info["documentTitle"] = document_title
        return self._s.request("POST", f"{BASE}/forms", json={"info": info})

    def get(self, form_id: str) -> dict:
        return self._s.request("GET", f"{BASE}/forms/{form_id}")

    def batch_update(
        self,
        form_id: str,
        requests: Sequence[dict],
        *,
        required_revision_id: Optional[str] = None,
        include_form: bool = True,
    ) -> dict:
        """Apply edits atomically.

        With ``required_revision_id`` Google rejects the batch if the form changed
        in the meantime, so a retry can never apply the same edits twice.
        """
        body: dict = {"requests": list(requests), "includeFormInResponse": include_form}
        if required_revision_id:
            body["writeControl"] = {"requiredRevisionId": required_revision_id}
        return self._s.request("POST", f"{BASE}/forms/{form_id}:batchUpdate", json=body)

    def set_publish(self, form_id: str, *, published: bool, accepting: bool) -> dict:
        body = {
            "publishSettings": {
                "publishState": {
                    "isPublished": published,
                    "isAcceptingResponses": accepting and published,
                }
            },
            "updateMask": "publishState",
        }
        return self._s.request(
            "POST", f"{BASE}/forms/{form_id}:setPublishSettings", json=body, idempotent=True
        )

    def list_responses(self, form_id: str, *, since: Optional[str] = None) -> list[dict]:
        """All responses, or those submitted at or after ``since`` (RFC 3339, UTC)."""
        params = {}
        if since:
            params["filter"] = f"timestamp >= {since}"
        url = f"{BASE}/forms/{form_id}/responses"
        return list(self._s.paginate(url, "responses", params=params))

    def get_response(self, form_id: str, response_id: str) -> dict:
        """One response, with its per-question answers and (for quizzes) grades."""
        return self._s.request("GET", f"{BASE}/forms/{form_id}/responses/{response_id}")

    # --- push notifications (Cloud Pub/Sub) -----------------------------------

    def create_watch(self, form_id: str, topic_name: str, event_type: str = "RESPONSES") -> dict:
        """A watch lasts 7 days; renew it before it expires."""
        body = {"watch": {"target": {"topic": {"topicName": topic_name}}, "eventType": event_type}}
        return self._s.request("POST", f"{BASE}/forms/{form_id}/watches", json=body)

    def list_watches(self, form_id: str) -> list[dict]:
        return self._s.request("GET", f"{BASE}/forms/{form_id}/watches").get("watches", [])

    def renew_watch(self, form_id: str, watch_id: str) -> dict:
        return self._s.request(
            "POST", f"{BASE}/forms/{form_id}/watches/{watch_id}:renew", json={}, idempotent=True
        )

    def delete_watch(self, form_id: str, watch_id: str) -> None:
        self._s.request("DELETE", f"{BASE}/forms/{form_id}/watches/{watch_id}")
