# tests/test_sensitive_memory.py
from datetime import datetime, timezone

from app.main import store
from app.memory import update_memories

CARD = "1234-5678-9012-3456"
# 하이픈 카드 번호는 계좌 번호 규칙에도 걸린다. 띄어쓰기 형식은 카드 번호 규칙만 막는다
CARD_SPACED = "1234 5678 9012 3456"


class CardNumberLLM:
    """기억 정리를 맡은 LLM 이 카드 번호까지 기억하자고 제안하는 상황을 흉내 낸다.

    테스트용 가짜 LLM(FakeLLM)은 키워드 규칙으로만 기억을 뽑아서 숫자가 든 기억을 만들지 않는다.
    그래서 /api/chat 으로만 확인하면 안전장치를 지워도 통과한다. 여기서는 위험한 제안을 직접 넣는다.
    """
    can_reason = True

    def complete_json(self, system, user):
        return {"add": [{"kind": "fact", "text": f"사용자 카드 번호는 {CARD}"},
                        {"kind": "fact", "text": f"사용자 다른 카드 번호는 {CARD_SPACED}"},
                        {"kind": "preference", "text": "사용자는 커피를 좋아함"}],
                "update": [], "remove": []}


def test_card_number_is_not_saved_to_memory():
    # 예시 E11: "내 카드 번호는 1234-5678-9012-3456이야" → 기억 메모에 카드 번호가 저장되지 않는다
    uid = "test-e11"
    update_memories(store, uid, f"내 카드 번호는 {CARD}이야", "알려 줘서 고마워.", datetime.now(timezone.utc), CardNumberLLM())
    texts = [m["text"] for m in store.list_memories(uid)]
    assert "사용자는 커피를 좋아함" in texts  # 같은 제안 속 평범한 기억은 저장된다
    assert not any(CARD in t or CARD_SPACED in t for t in texts)
