"""화면의 반응(좋아요, 별로예요, 신고)을 그 답의 트레이스 점수로 보낸다. 남의 메시지에는 남길 수 없다."""
from fastapi.testclient import TestClient

from app import main, tracing
from app.main import app

client = TestClient(app)


def last_reply_id():
    client.post("/api/chat", json={"message": "안녕"})
    return [m for m in client.get("/api/messages").json() if m["role"] == "assistant"][-1]["id"]


def test_feedback_on_my_reply_sends_user_feedback_score(monkeypatch):
    sent = []
    monkeypatch.setattr(tracing, "score", lambda trace, name, value, comment=None: sent.append((name, value)) or True)
    mid = last_reply_id()
    assert client.post("/api/feedback", json={"message_id": mid, "liked": False}).json() == {"ok": True}
    assert client.post("/api/report", json={"message_id": mid, "reason": "엉뚱해요"}).json() == {"ok": True}
    assert sent == [("user-feedback", False), ("report", True)]


def test_feedback_without_keys_still_answers_ok():
    assert tracing.client() is None
    assert client.post("/api/feedback", json={"message_id": last_reply_id(), "liked": True}).status_code == 200


def test_cannot_react_to_someone_elses_message():
    other = main.store.add_message("someone-else", "assistant", "남의 답", {"trace_id": "t-other"})
    assert client.post("/api/feedback", json={"message_id": other["id"], "liked": False}).status_code == 404
    assert client.post("/api/report", json={"message_id": other["id"], "reason": ""}).status_code == 404


def test_cannot_react_to_my_own_message():
    client.post("/api/chat", json={"message": "안녕"})
    mine = [m for m in client.get("/api/messages").json() if m["role"] == "user"][-1]["id"]
    assert client.post("/api/feedback", json={"message_id": mine, "liked": True}).status_code == 404


def test_report_comment_is_masked_before_sending(monkeypatch):
    sent = {}

    class Fake:
        def create_score(self, **kwargs):
            sent.update(kwargs)

        def flush(self):
            pass

    monkeypatch.setattr(tracing, "client", lambda: Fake())
    assert tracing.score("t1", "report", True, comment="제 번호 010-1234-5678 이 나왔어요")
    assert sent["comment"] == "제 번호 *** 이 나왔어요"
    assert sent["value"] == 1 and sent["data_type"] == "BOOLEAN" and sent["trace_id"] == "t1"
