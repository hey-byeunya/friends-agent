"""Langfuse 계측: 키가 없으면 꺼지고, 보내기 전에 개인정보 패턴을 가린다."""
from fastapi.testclient import TestClient

from app import tracing
from app.llm import FakeLLM
from app.main import app


def test_tracing_is_off_without_keys():
    assert tracing.client() is None
    assert tracing.start_trace("chat-turn", input="안녕") is tracing.OFF


def test_turn_still_runs_without_keys():
    response = TestClient(app).post("/api/chat", json={"message": "안녕"})
    assert response.status_code == 200
    assert '"type": "done"' in response.text


def test_mask_text_hides_phone_email_card():
    text = "전화 010-1234-5678, 메일 friend@example.com, 카드 1234 5678 9012 3456 로 보내 줘"
    masked = tracing.mask_text(text)
    assert "010-1234-5678" not in masked
    assert "friend@example.com" not in masked
    assert "1234 5678 9012 3456" not in masked
    assert masked.count(tracing.MASK) == 3


def test_mask_text_keeps_ordinary_numbers():
    text = "내일 8시에 물 2잔, 기온 23도, 2026-11-02"
    assert tracing.mask_text(text) == text


def test_mask_pii_patches_only_changed_attributes():
    from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier

    span_id = OtelSpanIdentifier(trace_id="t1", span_id="s1")
    span = OtelSpanData(trace_id="t1", span_id="s1", parent_span_id=None, name="reply",
                        instrumentation_scope_name=None, instrumentation_scope_version=None,
                        attributes={"langfuse.observation.input": "메일은 a@b.co 야",
                                    "langfuse.observation.model.name": "gpt-4o-mini"},
                        resource_attributes={})
    result = tracing.mask_pii(params=MaskOtelSpansParams(spans={span_id: span}))
    assert dict(result.span_patches[span_id].set_attributes) == {"langfuse.observation.input": "메일은 *** 야"}


def test_fake_llm_reports_zero_usage():
    usage = {}
    "".join(FakeLLM(delay=0).chat_stream([{"role": "system", "content": "이름: 하루"}, {"role": "user", "content": "안녕"}], usage=usage))
    assert usage == {"input": 0, "output": 0}


def test_turn_attributes_use_user_id_and_korean_date(monkeypatch):
    from datetime import datetime

    monkeypatch.delenv("K_REVISION", raising=False)
    monkeypatch.setenv("COMMIT_SHA", "abcdef1234567890")
    attrs = tracing.turn_attributes("user-1")
    today = datetime.now(tracing.KST).date().isoformat()
    assert attrs["user_id"] == "user-1"
    assert attrs["session_id"] == f"user-1-{today}"
    assert attrs["version"] == "local"
    assert attrs["metadata"] == {"commit": "abcdef123456"}


def test_turn_fills_tags_and_metadata_after_choosing(monkeypatch):
    from app import agent
    from app.router import ToolChoice
    from app.store.memory_store import MemoryStore

    seen = {}
    original = tracing.turn_attributes

    def keep(uid):  # _turn 이 채우는 attrs 를 테스트에서 들여다본다
        seen.update(original(uid))
        return seen

    monkeypatch.setattr(tracing, "turn_attributes", keep)
    monkeypatch.setattr(agent, "choose", lambda *a: ToolChoice([], 0.3, "", "model", action="clarify"))
    events = list(agent.run_turn("음", "user-1", FakeLLM(delay=0), MemoryStore(), False))
    assert events[-1]["type"] == "done"
    assert seen["tags"] == ["clarify"]
    assert seen["metadata"]["safety"] is False
    assert seen["metadata"]["tools"] == "" and seen["metadata"]["confidence"] == 0.3


def test_turn_flushes_before_closing_response(monkeypatch):
    from app import agent
    from app.store.memory_store import MemoryStore

    calls = []
    monkeypatch.setattr(tracing, "flush", lambda: calls.append("flush"))
    events = list(agent.run_turn("안녕", "user-1", FakeLLM(delay=0), MemoryStore(), False))
    assert events[-1]["type"] == "done"
    assert calls == ["flush"]


def test_incoming_traceparent_does_not_drop_our_traces():
    """Cloud Run 은 요청에 traceparent 를 붙이고 일부에만 '기록함'(-01) 표시를 한다.
    FastAPI 가 그 헤더를 부모로 이으면 '기록 안 함'(-00) 요청의 트레이스가 통째로 사라진다."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    trace.set_tracer_provider(TracerProvider())  # Langfuse 클라이언트가 만들어질 때 하는 일

    @app.get("/test/recording")
    def recording():
        return {"recording": trace.get_tracer("test").start_span("probe").is_recording()}

    unsampled = {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-00"}
    assert TestClient(app).get("/test/recording", headers=unsampled).json() == {"recording": True}
