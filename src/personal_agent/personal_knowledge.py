"""Owner-local Personal Space retrieval with no model or network dependency."""
import hashlib
import re
import time
import uuid

from .context_inbox import SENSITIVE


class KnowledgeRetrievalError(ValueError):
    pass


_INJECTION = re.compile(r"(?:ignore (?:all |previous )?instructions|system\s*prompt|developer\s*message|jailbreak)", re.I)
_PATH = re.compile(r"(?:/Users/[^\s]+|[A-Za-z]:\\[^\s]+)")
_SPACE = re.compile(r"\s+")


def _normalized(value):
    return _SPACE.sub(" ", value.strip().casefold())


def _opaque(source, ident):
    return "pk_" + hashlib.sha256((source + "\0" + ident).encode()).hexdigest()[:20]


def _excerpt(value):
    value = _SPACE.sub(" ", value).strip()
    value = SENSITIVE.sub("[redacted sensitive value]", value)
    value = _PATH.sub("[redacted local path]", value)
    return value[:240]


class PersonalKnowledgeOrchestrator:
    """The sole policy owner for Personal Space retrieval.

    It intentionally takes a store, never a model, adapter, provider, or
    sharing callback.  The result envelope is safe to render but is not an
    authorization to send any result outside the owner runtime.
    """
    def __init__(self, store, now=time.time):
        self.store, self.now = store, now

    @staticmethod
    def _request(owner, channel, query):
        if not isinstance(owner, str) or not owner or len(owner) > 200:
            raise KnowledgeRetrievalError("소유자 식별을 확인하세요.")
        if not isinstance(channel, str) or channel not in ("http", "local-companion") and not channel.startswith("telegram:"):
            raise KnowledgeRetrievalError("요청 채널을 확인하세요.")
        if not isinstance(query, str):
            raise KnowledgeRetrievalError("찾을 내용을 입력하세요.")
        query = _normalized(query)
        if not 2 <= len(query) <= 160 or not any(char.isalnum() for char in query):
            raise KnowledgeRetrievalError("두 글자 이상의 구체적인 검색어를 입력하세요.")
        return query

    def _audit(self, terminal, categories=None, error_class=None, recovery=None):
        rows = self.store.config("personal_knowledge_audit", [])
        rows = rows if isinstance(rows, list) else []
        event = {"at": self.now(), "retrieval_ref": "kr_" + uuid.uuid4().hex,
                 "categories": dict(categories or {}), "terminal": terminal}
        if error_class: event["error_class"] = error_class
        if recovery: event["recovery"] = recovery
        self.store.put("personal_knowledge_audit", [*rows, event][-100:])
        return event

    def _candidates(self):
        """Read only existing local records; context payloads are never read."""
        now = self.now()
        with self.store.db() as db:
            db.execute("DELETE FROM context_events WHERE expires_at<=?", (now,))
            memories = [dict(row) for row in db.execute("SELECT id,content,created FROM notes ORDER BY created DESC LIMIT 100")]
            results = [dict(row) for row in db.execute("""
                SELECT r.id,r.content,r.created,w.title,w.purpose
                FROM workspace_results r JOIN workspaces w ON w.id=r.workspace_id
                ORDER BY r.created DESC LIMIT 100
            """)]
            # Context is represented only if a future local policy explicitly
            # marks metadata approved. Content is deliberately not selected.
            contexts = [dict(row) for row in db.execute("""
                SELECT id,captured_at,source_kind,sharing_state,source_app,source_domain
                FROM context_events WHERE sharing_state='approved-metadata'
                ORDER BY captured_at DESC LIMIT 100
            """)]
        rows = []
        for item in memories:
            rows.append({"source":"memory", "id":item["id"], "created":item["created"],
                         "title":item["content"][:120], "summary":item["content"]})
        for item in results:
            rows.append({"source":"workspace-result", "id":item["id"], "created":item["created"],
                         "title":item["title"] or "saved workspace result",
                         "summary":(item["purpose"] or "") + " " + item["content"]})
        for item in contexts:
            metadata = " ".join(part for part in (item["source_kind"], item["source_app"], item["source_domain"], "approved metadata") if part)
            rows.append({"source":"approved-context-metadata", "id":item["id"], "created":item["captured_at"],
                         "title":item["source_kind"] + " context", "summary":metadata, "metadata":True})
        return rows

    def retrieve(self, owner, channel, query):
        try:
            query = self._request(owner, channel, query)
        except KnowledgeRetrievalError as exc:
            return {"state":"blocked", "error_class":"invalid-request", "response":str(exc),
                    "recovery":"소유자, 채널, 검색어를 확인한 뒤 새 요청을 만드세요."}
        matches = []
        for item in self._candidates():
            haystack = _normalized(item["title"] + " " + item["summary"])
            if query not in haystack:
                continue
            if not item.get("metadata") and (_INJECTION.search(item["summary"]) or SENSITIVE.search(item["summary"])):
                audit = self._audit("blocked", error_class="unsafe-source", recovery="원본을 검토하거나 삭제한 뒤 새 요청을 만드세요.")
                return {"state":"blocked", "error_class":"unsafe-source", "response":"안전하게 표시할 수 없는 저장 항목이 있습니다.",
                        "recovery":audit["recovery"]}
            matches.append(item)
        if not matches:
            audit = self._audit("empty", recovery="원본이 삭제되었거나 일치 항목이 없습니다. 더 구체적인 검색어로 새 검색을 실행하세요.")
            return {"state":"empty", "results":[], "response":"일치하는 개인 지식을 찾지 못했습니다.",
                    "recovery":audit["recovery"], "audit":audit}
        matches.sort(key=lambda item: (-float(item["created"]), _opaque(item["source"], item["id"]), item["source"]))
        results = []
        for item in matches[:10]:
            results.append({"source":item["source"], "reference":_opaque(item["source"], item["id"]),
                            "captured_at":item["created"], "match_reason":"exact normalized term match",
                            "excerpt":("[approved context metadata] " + _excerpt(item["summary"]) if item.get("metadata") else _excerpt(item["summary"])),
                            "recovery":"원본이 삭제되었으면 새 검색을 실행하세요."})
        categories = {source:sum(1 for item in results if item["source"] == source) for source in sorted({item["source"] for item in results})}
        audit = self._audit("completed", categories)
        return {"state":"completed", "results":results, "response":"개인 공간에서 관련된 저장 항목을 찾았습니다.",
                "recovery":"이 결과는 로컬 읽기 전용입니다. 외부 공유에는 별도 승인이 필요합니다.", "audit":audit}
