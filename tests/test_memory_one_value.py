# tests/test_memory_one_value.py
from datetime import datetime, timezone

from app.main import store
from app.memory import update_memories


class PlanLLM:
    """기억 정리를 맡은 LLM 이 정해 둔 계획을 그대로 돌려준다.

    staging 트레이스(2026-11-02 16:39)에서 실제로 나온 계획: 「이름: 한수」가 있는데 「이름: 지수」를 add 하고,
    엉뚱한 기억을 같은 글자 그대로 update 했다.
    """
    can_reason = True

    def __init__(self, plan):
        self.plan = plan

    def complete_json(self, system, user):
        return self.plan


def remember(uid, *items):
    return [store.add_memory(uid, kind, text) for kind, text in items]


def test_new_value_for_same_key_replaces_old_one():
    uid = "test-one-name"
    cat, name, home = remember(uid, ("preference", "고양이를 좋아함"), ("fact", "이름: 한수"), ("fact", "거주지: 뉴욕"))
    plan = {"add": [{"kind": "fact", "text": "이름: 지수"}], "update": [{"id": cat["id"], "text": "고양이를 좋아함"}], "remove": []}
    changes = update_memories(store, uid, "내 이름은 지수야. 고양이를 좋아해", "안녕, 지수야!", datetime.now(timezone.utc), PlanLLM(plan))
    texts = [m["text"] for m in store.list_memories(uid)]
    assert texts.count("이름: 지수") == 1 and "이름: 한수" not in texts  # 이름은 하나만 남는다
    assert changes["added"] == []
    assert changes["updated"] == [{"id": name["id"], "text": "이름: 지수"}]  # 같은 글자 update(고양이)는 바뀐 것으로 세지 않는다


def test_different_keys_are_still_added():
    uid = "test-other-key"
    remember(uid, ("fact", "이름: 한수"))
    plan = {"add": [{"kind": "fact", "text": "직업: 디자이너"}], "update": [], "remove": []}
    update_memories(store, uid, "나 디자이너야", "멋지다!", datetime.now(timezone.utc), PlanLLM(plan))
    texts = [m["text"] for m in store.list_memories(uid)]
    assert "이름: 한수" in texts and "직업: 디자이너" in texts
