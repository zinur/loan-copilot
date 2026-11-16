"""Мінімальний клієнт CodeMie Workflow API (ті самі ендпоінти, що в офіційному codemie-sdk).

POST /v1/workflows/{id}/executions                        -> запуск (user_input)
GET  /v1/workflows/{id}/executions/{exec}                 -> статус (overall_status)
GET  /v1/workflows/{id}/executions/{exec}/states          -> стани вузлів
GET  /v1/workflows/{id}/executions/{exec}/states/{s}/output -> відповідь вузла

Env: CODEMIE_API_URL (напр. https://<host>/code-assistant-api), CODEMIE_WORKFLOW_ID,
     CODEMIE_TOKEN  АБО  CODEMIE_AUTH_URL + CODEMIE_CLIENT_ID + CODEMIE_CLIENT_SECRET (Keycloak client_credentials).
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

TERMINAL = {"Succeeded", "Failed", "Aborted", "Interrupted"}

_VERDICT_KEYS = ("decision", "verdict", "recommendation")
_KEYWORDS = [  # порядок важливий: специфічніші першими
    ("refuse_injection", ("refuse_injection", "відмовля", "не можу виконати")),
    ("hitl_escalate", ("hitl_escalate", "escalate", "ескалац", "ручн", "manual review")),
    ("reject", ("reject", "відмов", "не схвал", "decline")),
    ("approve", ("approve", "схвал", "можна видати", "approved")),
]


def parse_verdict(text: str) -> str | None:
    """Детермінований розбір вердикту: спершу JSON-поле, потім ключові слова."""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            for k in _VERDICT_KEYS:
                if isinstance(obj.get(k), str):
                    return obj[k].strip().lower()
        except (ValueError, AttributeError):
            pass
    low = text.lower()
    for verdict, words in _KEYWORDS:
        if any(w in low for w in words):
            return verdict
    return None


class CodeMieWorkflowClient:
    def __init__(self, api_url: str, workflow_id: str, *, session: requests.Session | None = None,
                 poll_interval: float = 2.0, timeout: float = 180.0):
        self.api_url = api_url.rstrip("/")
        self.workflow_id = workflow_id
        self.http = session or requests.Session()
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._token: str | None = os.environ.get("CODEMIE_TOKEN")

    @classmethod
    def from_env(cls, workflow_id: str | None = None) -> "CodeMieWorkflowClient":
        url = os.environ.get("CODEMIE_API_URL")
        wf = workflow_id or os.environ.get("CODEMIE_WORKFLOW_ID")
        if not url or not wf:
            raise RuntimeError("Потрібні CODEMIE_API_URL і CODEMIE_WORKFLOW_ID (або --workflow-id).")
        return cls(url, wf)

    def _headers(self) -> dict:
        cookie = os.environ.get("CODEMIE_COOKIE")  # сесія oauth2-proxy з браузера (лише для демо)
        if cookie and not self._token:
            return {"Cookie": cookie, "Content-Type": "application/json"}
        if not self._token:
            auth_url = os.environ.get("CODEMIE_AUTH_URL")
            if not auth_url:
                raise RuntimeError("Потрібен CODEMIE_TOKEN або CODEMIE_AUTH_URL + CLIENT_ID + CLIENT_SECRET.")
            r = self.http.post(auth_url, data={
                "grant_type": "client_credentials",
                "client_id": os.environ["CODEMIE_CLIENT_ID"],
                "client_secret": os.environ["CODEMIE_CLIENT_SECRET"],
            }, timeout=30)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, suffix: str = "") -> str:
        return f"{self.api_url}/v1/workflows/{self.workflow_id}/executions{suffix}"

    def run(self, user_input: str, tags: list[str] | None = None) -> dict:
        """Запускає виконання, чекає завершення, повертає {status, output, execution_id}."""
        r = self.http.post(self._url(), headers=self._headers(), timeout=60,
                           json={"user_input": user_input, "tags": tags or ["w4-golden-eval"], "propagate_headers": False})
        r.raise_for_status()
        body = r.json()
        # Точна форма відповіді залежить від версії стенду — читаємо захисно.
        exec_id = next((body[k] for k in ("execution_id", "executionId", "id", "taskId") if body.get(k)), None)
        if not exec_id:
            raise RuntimeError(f"Не знайшов id виконання у відповіді: {body}")

        deadline = time.monotonic() + self.timeout
        status = None
        while time.monotonic() < deadline:
            g = self.http.get(self._url(f"/{exec_id}"), headers=self._headers(), timeout=30)
            g.raise_for_status()
            status = g.json().get("overall_status") or g.json().get("status")
            if status in TERMINAL:
                break
            time.sleep(self.poll_interval)
        else:
            return {"status": "Timeout", "output": "", "execution_id": exec_id}

        output = ""
        if status == "Succeeded":
            s = self.http.get(self._url(f"/{exec_id}/states"), headers=self._headers(),
                              params={"page": 0, "per_page": 50}, timeout=30)
            s.raise_for_status()
            body = s.json()
            if isinstance(body, dict):  # стенд може обгортати список: {"data": [...]} тощо
                body = next((body[k] for k in ("data", "items", "states", "results") if isinstance(body.get(k), list)), None)
            if not isinstance(body, list):
                raise RuntimeError(f"Незнайома форма відповіді /states: {s.text[:500]}")
            states = sorted((x for x in body if isinstance(x, dict)), key=lambda x: x.get("completed_at") or "")
            wanted = os.environ.get("CODEMIE_RESULT_STATE", "policy-answer")
            # Останній вузол часто — автоматичний «Conversation Summary». Беремо вузол з відповіддю:
            # спершу за назвою, далі — перший з кінця, чий output містить JSON із полем decision.
            ordered = sorted(states, key=lambda x: (wanted not in (x.get("name"), x.get("id"))))[:1] + states[::-1]
            for st in ordered:
                o = self.http.get(self._url(f"/{exec_id}/states/{st['id']}/output"),
                                  headers=self._headers(), timeout=30)
                o.raise_for_status()
                text = o.json().get("output") or ""
                if re.search(r'"decision"\s*:', text):
                    output = text
                    break
                output = output or text
            try:  # декодуємо \uXXXX: читабельний вивід і коректні метрики на справжньому тексті
                output = json.dumps(json.loads(output), ensure_ascii=False)
            except ValueError:
                pass
            # якщо вузла з decision нема — output лишається першим непорожнім; parse_verdict тоді ненадійний
        return {"status": status, "output": output, "execution_id": exec_id}
