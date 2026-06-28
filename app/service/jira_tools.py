from typing import Any

import httpx

ATLASSIAN_API_URL = "https://api.atlassian.com"


def _text_to_adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _adf_to_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    parts: list[str] = []
    for child in node.get("content") or []:
        parts.append(_adf_to_text(child))
    if node.get("type") == "paragraph":
        parts.append("\n")
    return "".join(parts)


def _summarize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    issue_type = fields.get("issuetype") or {}
    priority = fields.get("priority") or {}
    assignee = fields.get("assignee") or {}
    project = fields.get("project") or {}
    description = fields.get("description")
    description_text = _adf_to_text(description).strip() if description else ""

    return {
        "key": issue.get("key"),
        "id": issue.get("id"),
        "summary": fields.get("summary"),
        "description": description_text or None,
        "status": status.get("name"),
        "issue_type": issue_type.get("name"),
        "priority": priority.get("name"),
        "assignee": assignee.get("displayName") if assignee else None,
        "project_key": project.get("key"),
        "project_name": project.get("name"),
        "url": issue.get("self"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
    }


class JiraTools:
    def __init__(self, *, access_token: str, cloud_id: str, site_url: str) -> None:
        self._access_token = access_token
        self._cloud_id = cloud_id
        self._site_url = site_url.rstrip("/")
        self._base = f"{ATLASSIAN_API_URL}/ex/jira/{cloud_id}/rest/api/3"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        with httpx.Client(timeout=20.0) as client:
            response = client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
            )
        if response.status_code == 204:
            return {"ok": True}
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}

        if response.status_code >= 400:
            messages = data.get("errorMessages") if isinstance(data, dict) else None
            errors = data.get("errors") if isinstance(data, dict) else None
            detail = (
                "; ".join(messages)
                if messages
                else str(errors)
                if errors
                else data.get("message")
                if isinstance(data, dict)
                else response.text
            )
            raise RuntimeError(detail or f"Jira API error ({response.status_code})")
        return data

    def get_site(self) -> dict[str, str]:
        return {"site_url": self._site_url, "cloud_id": self._cloud_id}

    def list_projects(self, *, max_results: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 100))
        data = self._request(
            "GET",
            "/project/search",
            params={"maxResults": limit, "orderBy": "name"},
        )
        values = data.get("values") or []
        return [
            {
                "key": project.get("key"),
                "name": project.get("name"),
                "id": project.get("id"),
                "project_type": project.get("projectTypeKey"),
            }
            for project in values
        ]

    def search_issues(self, jql: str, *, max_results: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 50))
        data = self._request(
            "POST",
            "/search/jql",
            json_body={
                "jql": jql,
                "maxResults": limit,
                "fields": [
                    "summary",
                    "status",
                    "issuetype",
                    "priority",
                    "assignee",
                    "project",
                    "description",
                    "created",
                    "updated",
                ],
            },
        )
        issues = data.get("issues") or []
        return [_summarize_issue(issue) for issue in issues]

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/issue/{issue_key}",
            params={"fields": "summary,description,status,issuetype,priority,assignee,project,created,updated"},
        )
        summary = _summarize_issue(data)
        summary["browse_url"] = f"{self._site_url}/browse/{issue_key}"
        return summary

    def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str,
        *,
        description: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = _text_to_adf(description)
        if priority:
            fields["priority"] = {"name": priority}

        data = self._request("POST", "/issue", json_body={"fields": fields})
        issue_key = data.get("key")
        return {
            "key": issue_key,
            "id": data.get("id"),
            "browse_url": f"{self._site_url}/browse/{issue_key}" if issue_key else None,
            "message": f"Created issue {issue_key}" if issue_key else "Issue created",
        }

    def update_issue(
        self,
        issue_key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = _text_to_adf(description)
        if priority is not None:
            fields["priority"] = {"name": priority}

        if not fields:
            raise ValueError("Provide at least one field to update (summary, description, or priority)")

        self._request("PUT", f"/issue/{issue_key}", json_body={"fields": fields})
        return {
            "key": issue_key,
            "browse_url": f"{self._site_url}/browse/{issue_key}",
            "message": f"Updated issue {issue_key}",
            "updated_fields": list(fields.keys()),
        }

    def delete_issue(self, issue_key: str) -> dict[str, Any]:
        self._request("DELETE", f"/issue/{issue_key}", params={"deleteSubtasks": "true"})
        return {"key": issue_key, "message": f"Deleted issue {issue_key}"}
