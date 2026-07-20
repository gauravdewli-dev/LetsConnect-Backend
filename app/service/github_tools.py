from typing import Any
from urllib.parse import urlparse

import httpx

from app.constants import GITHUB_API_URL, GITHUB_API_VERSION


def _summarize_repo(repo: dict[str, Any], *, viewer_login: str | None = None) -> dict[str, Any]:
    owner = repo.get("owner") or {}
    owner_login = owner.get("login")
    permissions = repo.get("permissions") or {}
    if viewer_login and owner_login == viewer_login:
        access = "owner"
    elif permissions.get("admin"):
        access = "admin"
    elif permissions.get("maintain"):
        access = "maintain"
    elif permissions.get("push"):
        access = "collaborator"
    elif permissions.get("pull"):
        access = "read"
    else:
        access = "member" if owner.get("type") == "Organization" else "collaborator"

    return {
        "full_name": repo.get("full_name"),
        "name": repo.get("name"),
        "owner": owner_login,
        "private": repo.get("private"),
        "fork": repo.get("fork"),
        "access": access,
        "description": repo.get("description"),
        "default_branch": repo.get("default_branch"),
        "html_url": repo.get("html_url"),
        "updated_at": repo.get("updated_at"),
    }


def _repo_full_name_from_pr(pr: dict[str, Any]) -> str | None:
    base = pr.get("base") or {}
    repo = base.get("repo") or pr.get("repository") or {}
    if isinstance(repo, dict) and repo.get("full_name"):
        return repo.get("full_name")
    repository_url = pr.get("repository_url") or ""
    if "/repos/" in repository_url:
        return repository_url.split("/repos/", 1)[-1]
    html_url = pr.get("html_url") or ""
    if "github.com/" in html_url and "/pull/" in html_url:
        path = urlparse(html_url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def _summarize_pr(pr: dict[str, Any], *, body_limit: int = 2000) -> dict[str, Any]:
    user = pr.get("user") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    body = (pr.get("body") or "").strip()
    if body_limit and len(body) > body_limit:
        body = body[:body_limit] + "…"

    labels = []
    for label in pr.get("labels") or []:
        if isinstance(label, dict) and label.get("name"):
            labels.append(label["name"])
        elif isinstance(label, str):
            labels.append(label)

    assignees = [
        a.get("login")
        for a in (pr.get("assignees") or [])
        if isinstance(a, dict) and a.get("login")
    ]
    reviewers = [
        r.get("login")
        for r in (pr.get("requested_reviewers") or [])
        if isinstance(r, dict) and r.get("login")
    ]

    full_name = _repo_full_name_from_pr(pr)
    number = pr.get("number")

    return {
        "number": number,
        "title": pr.get("title"),
        "description": body or None,
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "author": user.get("login"),
        "user": user.get("login"),
        "head": head.get("ref") if isinstance(head, dict) else None,
        "base": base.get("ref") if isinstance(base, dict) else None,
        "labels": labels or None,
        "assignees": assignees or None,
        "requested_reviewers": reviewers or None,
        "repo": full_name,
        "html_url": pr.get("html_url"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "body": body or None,
    }


def _summarize_search_pr(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") or {}
    body = (item.get("body") or "").strip()
    if len(body) > 800:
        body = body[:800] + "…"
    full_name = _repo_full_name_from_pr(item)
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "description": body or None,
        "state": item.get("state"),
        "draft": item.get("draft"),
        "author": user.get("login"),
        "repo": full_name,
        "html_url": item.get("html_url"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "body": body or None,
        "labels": [
            label.get("name")
            for label in (item.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        ]
        or None,
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

    def _resolve_login(self) -> str:
        if self._login:
            return self._login
        profile = self.get_me()
        login = profile.get("login")
        if not login:
            raise RuntimeError("Could not resolve connected GitHub login")
        self._login = login
        return login

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

    def _request_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int = 100,
    ) -> list[Any]:
        """Paginate GitHub list endpoints until max_items or no more pages."""
        page = 1
        per_page = min(100, max(1, max_items))
        collected: list[Any] = []
        base_params = dict(params or {})
        while len(collected) < max_items:
            page_params = {**base_params, "per_page": per_page, "page": page}
            data = self._request("GET", path, params=page_params)
            if not isinstance(data, list) or not data:
                break
            collected.extend(data)
            if len(data) < per_page:
                break
            page += 1
            if page > 10:
                break
        return collected[:max_items]

    def list_repos(
        self,
        *,
        query: str | None = None,
        affiliation: str | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List repos the authenticated user can access — owned, collaborator, and org member
        (including private). Paginate and merge affiliations so collaborator repos are not missed.
        """
        limit = max(1, min(max_results, 200))
        login = self._login or self._resolve_login()

        if query:
            # Prefer authenticated search scoped to user access; still may miss some private
            # collab repos — empty query path below is the reliable "all my repos" path.
            params: dict[str, Any] = {
                "q": f"{query} in:name fork:true",
                "per_page": min(100, limit),
            }
            data = self._request("GET", "/search/repositories", params=params)
            items = data.get("items") or []
            return [_summarize_repo(repo, viewer_login=login) for repo in items[:limit]]

        if affiliation:
            affiliations = [a.strip() for a in affiliation.split(",") if a.strip()]
        else:
            affiliations = ["owner", "collaborator", "organization_member"]

        by_id: dict[Any, dict[str, Any]] = {}
        for aff in affiliations:
            repos = self._request_pages(
                "/user/repos",
                params={
                    "affiliation": aff,
                    "visibility": "all",
                    "sort": "updated",
                    "direction": "desc",
                },
                max_items=limit,
            )
            for repo in repos:
                repo_id = repo.get("id") or repo.get("full_name")
                if repo_id is not None and repo_id not in by_id:
                    by_id[repo_id] = repo

        merged = sorted(
            by_id.values(),
            key=lambda r: r.get("updated_at") or "",
            reverse=True,
        )
        return [_summarize_repo(repo, viewer_login=login) for repo in merged[:limit]]

    def list_branches(
        self,
        owner: str,
        repo: str,
        *,
        query: str | None = None,
        max_results: int = 30,
    ) -> list[dict[str, Any]]:
        """List branch names for a repo; optional substring filter for dynamic head/base selection."""
        limit = max(1, min(max_results, 100))
        branches = self._request(
            "GET",
            f"/repos/{owner}/{repo}/branches",
            params={"per_page": min(100, max(limit, 30))},
        )
        if not isinstance(branches, list):
            return []
        results: list[dict[str, Any]] = []
        needle = (query or "").strip().lower()
        for branch in branches:
            name = branch.get("name")
            if not name:
                continue
            if needle and needle not in name.lower():
                continue
            results.append(
                {
                    "name": name,
                    "protected": branch.get("protected"),
                    "commit_sha": (branch.get("commit") or {}).get("sha"),
                }
            )
            if len(results) >= limit:
                break
        return results

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        head: str | None = None,
        base: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(max_results, 50))
        params: dict[str, Any] = {
            "state": state,
            "per_page": limit,
            "sort": "updated",
            "direction": "desc",
        }
        # GitHub expects head as user:ref for forks; accept bare branch for same-repo PRs.
        if head:
            params["head"] = head if ":" in head else f"{owner}:{head}"
        if base:
            params["base"] = base
        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params=params,
        )
        if not isinstance(data, list):
            return []
        return [_summarize_pr(pr, body_limit=600) for pr in data]

    def search_my_pull_requests(
        self,
        *,
        role: str = "authored",
        state: str = "open",
        repo: str | None = None,
        query: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Find PRs involving the connected user across repos.

        role:
          - authored: PRs the user opened
          - review_requested: PRs waiting on their review
          - assigned: PRs assigned to them
          - involves: any PR they authored, are assigned to, mentioned in, or asked to review
        """
        login = self._resolve_login()
        role_key = (role or "authored").strip().lower().replace("-", "_")
        role_clause = {
            "authored": f"author:{login}",
            "author": f"author:{login}",
            "review_requested": f"review-requested:{login}",
            "reviewrequested": f"review-requested:{login}",
            "assigned": f"assignee:{login}",
            "assignee": f"assignee:{login}",
            "involves": f"involves:{login}",
        }.get(role_key, f"author:{login}")

        parts = ["is:pr", role_clause]
        state_key = (state or "open").strip().lower()
        if state_key == "open":
            parts.append("is:open")
        elif state_key == "closed":
            parts.append("is:closed")
        elif state_key == "merged":
            parts.append("is:merged")
        if repo:
            parts.append(f"repo:{repo}")
        if query:
            parts.append(query)

        limit = max(1, min(max_results, 50))
        data = self._request(
            "GET",
            "/search/issues",
            params={
                "q": " ".join(parts),
                "per_page": limit,
                "sort": "updated",
                "order": "desc",
            },
        )
        items = data.get("items") or []
        return [_summarize_search_pr(item) for item in items]

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")
        summary = _summarize_pr(data, body_limit=8000)
        # Lightweight review snapshot for "review this PR" asks.
        try:
            reviews = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews")
            if isinstance(reviews, list):
                summary["reviews"] = [
                    {
                        "user": (review.get("user") or {}).get("login"),
                        "state": review.get("state"),
                        "body": ((review.get("body") or "").strip()[:400] or None),
                        "submitted_at": review.get("submitted_at"),
                    }
                    for review in reviews[-10:]
                ]
        except RuntimeError:
            summary["reviews"] = None
        return summary

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
        return _summarize_pr(data, body_limit=8000)

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
