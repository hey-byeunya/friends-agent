"""Langfuse로 한 턴의 단계를 트레이스로 남긴다.

LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 가 둘 다 있을 때만 켜진다. 없으면 모든 함수가 아무것도 하지 않는
가짜 관찰 단위를 돌려주므로, 키를 넣지 않은 컴퓨터나 테스트에서도 앱은 그대로 돈다.

트레이스에는 대화 원문이 올라간다. 보내기 직전에 전화번호, 이메일, 카드 번호 패턴은 가린다(mask_pii).
서버 로그(app/logs.py)에는 지금처럼 원문을 남기지 않는다.

근거 문서 (Python SDK v5)
- https://langfuse.com/docs/observability/sdk/overview
- https://langfuse.com/docs/observability/sdk/instrumentation
- https://langfuse.com/docs/observability/features/masking
- https://langfuse.com/docs/observability/features/token-and-cost-tracking
- https://langfuse.com/docs/observability/features/users
- https://langfuse.com/docs/observability/features/sessions
- https://langfuse.com/docs/observability/features/environments
- https://langfuse.com/docs/observability/features/releases-and-versioning
- https://langfuse.com/docs/observability/features/tags
- https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk
- https://langfuse.com/docs/observability/features/user-feedback
"""
import os
import re
from contextlib import contextmanager, nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

from app.logs import log

KST = ZoneInfo("Asia/Seoul")
MASK = "***"
PII_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                       # 이메일
    re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{1,4}(?!\d)"),              # 카드 번호 13~16자리
    re.compile(r"(?<!\d)(?:\+82[ -]?|0)1[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"),  # 휴대전화
]


def mask_text(text):
    for pattern in PII_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def mask_pii(*, params):
    """mask_otel_spans 훅. 내보내기 직전의 문자열 속성에서 개인정보 패턴을 가린다.

    v5 에서는 입력, 출력, metadata 가 JSON 문자열 속성으로 들어 있으므로 문자열만 보면 된다.
    """
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        changed = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                masked = mask_text(value)
                if masked != value:
                    changed[key] = masked
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)


class _Off:
    """계측이 꺼졌을 때 쓰는 관찰 단위. 어떤 호출도 무시한다."""

    def update(self, **kwargs):
        return self

    def end(self, **kwargs):
        return self

    def start_observation(self, **kwargs):
        return self

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield self


OFF = _Off()
_client = None


def client():
    """키가 둘 다 있으면 Langfuse 클라이언트, 없으면 None. 처음 부를 때 한 번만 만든다."""
    global _client
    if _client is None:
        if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
            _client = False
        else:
            try:
                from langfuse import Langfuse

                _client = Langfuse(mask_otel_spans=mask_pii, environment=os.environ.get("ENV", "local"))
            except Exception as error:  # 계측이 실패해도 서비스는 멈추지 않는다
                log("WARNING", "Langfuse 연결 실패, 계측을 끕니다", error=type(error).__name__)
                _client = False
    return _client or None


def start_trace(name, input=None, as_type="span"):
    """트레이스의 루트 관찰 단위를 연다. 끝낼 때 .end() 를 불러야 한다."""
    c = client()
    return c.start_observation(name=name, as_type=as_type, input=input) if c else OFF


def current_trace(name, **kwargs):
    """yield 가 없는 함수에서 트레이스를 "지금 열린 관찰 단위"로 연다. with 문으로 쓴다."""
    c = client()
    return c.start_as_current_observation(name=name, **kwargs) if c else OFF.start_as_current_observation()


def turn_attributes(uid):
    """트레이스에 붙일 속성. 사용자 ID 만 넣고 이메일은 넣지 않는다. 세션은 사용자 ID + 한국 날짜다.

    environment 는 클라이언트를 만들 때 ENV 로 이미 붙인다. tags 와 metadata 의 도구 정보는 턴 중간에 채운다.
    """
    today = datetime.now(KST).date().isoformat()
    return {"user_id": uid, "session_id": f"{uid}-{today}",
            "version": os.environ.get("K_REVISION", "local"),
            "metadata": {"commit": os.environ.get("COMMIT_SHA", "local")[:12]}, "tags": []}


def attributes(attrs):
    """with 안에서 새로 열리는 관찰 단위에 attrs 를 붙인다. 키가 없으면 아무것도 하지 않는다."""
    if not client():
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(**attrs)


def record_call(model, messages, output, input_tokens, output_tokens):
    """LLM 클래스가 모델을 부른 뒤 부른다. 지금 열린 generation 에 모델, 입출력, 토큰을 적는다."""
    c = client()
    if c:
        c.update_current_generation(model=model, input=messages, output=output,
                                    usage_details=usage(input_tokens, output_tokens))


def usage(input_tokens, output_tokens):
    return {"input": int(input_tokens or 0), "output": int(output_tokens or 0)}


def flush():
    """모아 둔 기록을 지금 보낸다. 보내기가 실패해도 응답은 그대로 끝낸다."""
    c = client()
    if c:
        try:
            c.flush()
        except Exception as error:
            log("WARNING", "Langfuse 기록 보내기 실패", error=type(error).__name__)


def trace_id(observation):
    """메시지 meta 에 저장해 둘 트레이스 ID. 계측이 꺼져 있으면 None."""
    return getattr(observation, "trace_id", None)


def score(trace, name, value, comment=None):
    """사용자 반응을 트레이스 점수(BOOLEAN)로 보낸다. 키나 트레이스 ID 가 없으면 건너뛴다.

    점수는 OTel 내보내기를 거치지 않으므로 마스킹 훅이 적용되지 않는다. comment 는 여기서 직접 가린다.
    """
    c = client()
    if not (c and trace):
        return False
    try:
        c.create_score(name=name, value=1 if value else 0, trace_id=trace, data_type="BOOLEAN",
                       comment=mask_text(comment) if comment else None)
        c.flush()
        return True
    except Exception as error:
        log("WARNING", "Langfuse 점수 보내기 실패", score=name, error=type(error).__name__)
        return False
