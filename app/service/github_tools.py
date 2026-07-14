from typing import Any

import httpx

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


def _summarize_repo(repo: dict[str, Any]) -> dict[str, Any]:
    owner = repo.get("owner") or {}
    return {
        "full_name": repo.get("full_name"),
        "name": repo.get("name"),
        "owner": owner.get("login"),
        "private": repo.get("private"),
        "description": repo.get("description"),
        "default_branch": repo.get("default_branch"),
        "html_url": repo.get("html_url"),
        "updated_at": repo.get("updated_at"),
    }


def _summarize_pr(pr: dict[str, Any]) -> dict[str, Any]:
    user = pr.get("user") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "user": user.get("login"),
        "head": head.get("ref"),
        "base": base.get("ref"),
        "html_url": pr.get("html_url"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "body": (pr.get("body") or "")[:500] or None,
    }


def _summarize_workflow_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "display_title": run.get("display_title"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
        "head_branch": run.get("head_branch"),
        "html_url": run.get("html_url"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "run_number": run.get("run_number"),
    }


class GithubTools:
    def __init__(self, *, access_token: str, login: str | None = None) -> None:
        self._access_token = access_token
        self._login = login

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{GITHUB_API_URL}{path}"
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
            message = None
            if isinstance(data, dict):
                message = data.get("message")
                errors = data.get("errors")
                if errors:
                    message = f"{message}: {errors}" if message else str(errors)
            raise RuntimeError(message or f"GitHub API error ({response.status_code})")
        return data

    def get_me(self) -> dict[str, Any]:
        data = self._request("GET", "/user")
        return {
            "id": str(data.get("id")) if data.get("id") is not None else None,
            "login": data.get("login"),
            "name": data.get("name"),
            "email": data.get("email"),
            "avatar_url": data.get("avatar_url"),
            "html_url": data.get("html_url"),
        }

    def list_repos(
        self,
        *,
        query: str | None = None,
        affiliation: str | None = None,
        max_results: int = 30,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 100))
        if query:
            params: dict[str, Any] = {
                "q": f"{query} in:name fork:true",
                "per_page": limit,
            }
            data = self._request("GET", "/search/repositories", params=params)
            items = data.get("items") or []
            return [_summarize_repo(repo) for repo in items]

        params = {
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        }
        if affiliation:
            params["affiliation"] = affiliation
        else:
            params["affiliation"] = "owner,collaborator,organization_member"
        repos = self._request("GET", "/user/repos", params=params)
        if not isinstance(repos, list):
            return []
        return [_summarize_repo(repo) for repo in repos]

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 50))
        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": limit, "sort": "updated", "direction": "desc"},
        )
        if not isinstance(data, list):
            return []
        return [_summarize_pr(pr) for pr in data]

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")
        return _summarize_pr(data)

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "draft": draft,
        }
        if body:
            payload["body"] = body
        data = self._request("POST", f"/repos/{owner}/{repo}/pulls", json_body=payload)
        return _summarize_pr(data)

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        merge_method: str = "merge",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        method = merge_method if merge_method in {"merge", "squash", "rebase"} else "merge"
        payload: dict[str, Any] = {"merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message
        data = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/merge",
            json_body=payload,
        )
        return {
            "merged": data.get("merged"),
            "message": data.get("message"),
            "sha": data.get("sha"),
            "html_url": f"https://github.com/{owner}/{repo}/pull/{pull_number}",
        }

    def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        *,
        branch: str | None = None,
        status: str | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 30))
        params: dict[str, Any] = {"per_page": limit}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status
        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            params=params,
        )
        runs = data.get("workflow_runs") or []
        return [_summarize_workflow_run(run) for run in runs]

    def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        data = self._request("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
        return _summarize_workflow_run(data)
