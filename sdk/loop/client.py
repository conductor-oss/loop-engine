"""Minimal Conductor REST client (stdlib only).

Workers use conductor-python; everything else the SDK needs — register workflow
definitions, start executions, poll state, complete a HUMAN task — is a handful of
REST calls, kept here so the SDK stays light and debuggable.

Config comes from the same environment the conductor CLI uses:
  CONDUCTOR_SERVER_URL   e.g. http://localhost:8080/api   (default)
  CONDUCTOR_AUTH_TOKEN   optional; sent as X-Authorization and Bearer
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class ConductorError(RuntimeError):
    """A Conductor API call failed (non-2xx or unreachable)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Conductor:
    def __init__(self, server_url=None, auth_token=None, timeout=30):
        base = server_url or os.environ.get("CONDUCTOR_SERVER_URL") or "http://localhost:8080/api"
        self.base = base.rstrip("/")
        self.token = auth_token or os.environ.get("CONDUCTOR_AUTH_TOKEN") or ""
        self.timeout = timeout

    # -- transport ---------------------------------------------------------
    def _request(self, method, path, body=None, parse=True):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "*/*")
        if self.token:
            req.add_header("X-Authorization", self.token)
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:500]
            except Exception:
                pass
            raise ConductorError(f"{method} {path} -> HTTP {e.code}: {detail}", status=e.code) from None
        except urllib.error.URLError as e:
            raise ConductorError(
                f"Conductor server unreachable at {self.base} ({e.reason}). "
                "Start one with `conductor server start` or set CONDUCTOR_SERVER_URL."
            ) from None
        if not parse:
            return raw
        return json.loads(raw) if raw.strip() else None

    # -- metadata ----------------------------------------------------------
    def get_workflow_def(self, name):
        """The registered definition, or None if the workflow does not exist."""
        try:
            return self._request("GET", f"/metadata/workflow/{urllib.parse.quote(name)}")
        except ConductorError as e:
            if e.status in (404, 500):  # OSS returns 404; some builds 500 on missing
                return None
            raise

    def register_workflow_def(self, definition):
        """Upsert one workflow definition (update first — create can't overwrite)."""
        try:
            self._request("PUT", "/metadata/workflow", body=[definition], parse=False)
        except ConductorError:
            self._request("POST", "/metadata/workflow", body=definition, parse=False)

    # -- execution ---------------------------------------------------------
    def start_workflow(self, name, input_data, version=1):
        """Start an execution; returns the workflowId."""
        body = {"name": name, "version": version, "input": input_data}
        return self._request("POST", "/workflow", body=body, parse=False).strip().strip('"')

    def get_execution(self, workflow_id, include_tasks=False):
        flag = "true" if include_tasks else "false"
        return self._request("GET", f"/workflow/{workflow_id}?includeTasks={flag}")

    def update_task(self, task_result):
        return self._request("POST", "/tasks", body=task_result, parse=False)

    def terminate_workflow(self, workflow_id, reason=""):
        q = urllib.parse.urlencode({"reason": reason}) if reason else ""
        self._request("DELETE", f"/workflow/{workflow_id}" + (f"?{q}" if q else ""), parse=False)
