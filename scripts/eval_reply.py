"""답 생성(reply) 평가. data/eval/reminder_reply.jsonl 의 항목마다 지금 코드의 시스템 프롬프트로 답을 만들고 규칙으로 채점한다.

문제 항목(kind=problem): 약속을 '방금' 저장한 턴. 답이 "이미 저장돼 있다"처럼 원래 있던 것처럼 말하면 실패.
  통과 조건 = 금지 표현(must_not) 없음 + 저장했다는 말(must) 있음 + 약속 시각(must_any) 중 하나 있음.
정상 항목(kind=normal): 원래 잘 되던 턴(날씨, 미세먼지, 공휴일, 약속 목록, 잡담, 기억, 지원 안 하는 지역).
  통과 조건 = 항목에 적힌 사실(must_all / must_any)을 말하고 금지 표현(must_not)이 없음.
banmal=True 인 항목은 기본 캐릭터(반말)대로 존댓말 어미가 없어야 한다.

LLM 답은 매번 달라서 항목마다 --repeat 번(기본 3) 만든다. 도구 선택·도구 실행은 하지 않고, 트레이스에 남은
도구 결과 문장을 그대로 넣어 답 생성 단계만 본다(처음 어긋난 단계가 reply 이기 때문).

실행:
  ENV=reply-eval python scripts/eval_reply.py --label before   # .env 의 LLM 사용, 비용 발생(항목 16 x 3 = 48번)
  python scripts/eval_reply.py --regrade ../runs/before.json  # 저장된 답을 지금 채점 규칙으로 다시 채점(호출 없음)
결과: --out 폴더(기본 ../runs)에 <label>.json 저장, 화면에 요약.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import tracing  # noqa: E402
from app.agent import system_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.persona import DEFAULT_CHARACTER  # noqa: E402

POLITE = re.compile(r"(습니다|습니까|세요|에요|예요|해요|어요|아요|죠)[.!?~ 😊]|(습니다|세요|어요|아요|에요|예요|해요)$")


def grade(reply, checks):
    why = []
    if checks.get("must_not") and re.search(checks["must_not"], reply):
        why.append(f"금지 표현: {re.search(checks['must_not'], reply).group(0)}")
    if checks.get("must") and not re.search(checks["must"], reply):
        why.append("저장했다는 말 없음")
    for word in checks.get("must_all", []):
        if word not in reply:
            why.append(f"빠짐: {word}")
    if checks.get("must_any") and not any(w in reply for w in checks["must_any"]):
        why.append(f"빠짐: {'/'.join(checks['must_any'])} 중 하나")
    if checks.get("banmal") and POLITE.search(reply):
        why.append(f"존댓말: {POLITE.search(reply).group(0).strip()}")
    return why


def summarize(results):
    summary = {}
    for kind in ("problem", "normal"):
        rs = [r for r in results if r["kind"] == kind]
        summary[kind] = {"passed": sum(r["passed"] for r in rs), "total": len(rs)}
    return summary


def regrade(path, rows):
    """저장된 답을 지금 채점 규칙으로 다시 채점해 같은 파일에 regraded 로 덧붙인다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["results"]:
        why = grade(r["reply"], rows[r["id"]]["checks"])
        if (not why) != r["passed"]:
            print(f"판정 바뀜 {r['id']} #{r['run']}: {r['reply'][:60]!r} {why}")
        r["passed"], r["why"] = not why, why
    data["regraded"] = summarize(data["results"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{data['label']} 다시 채점] {data['regraded']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    ap.add_argument("--regrade")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT.parent / "runs"))
    args = ap.parse_args()

    rows = [json.loads(line) for line in (ROOT / "data/eval/reminder_reply.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.regrade:
        regrade(Path(args.regrade), {r["id"]: r for r in rows})
        return
    llm = make_llm(load_settings())
    if not getattr(llm, "can_reason", True):
        print("LLM_PROVIDER=fake 에서는 평가할 수 없습니다.")
        return
    results = []
    for row in rows:
        now = datetime.fromisoformat(row["now"])
        system = system_prompt(DEFAULT_CHARACTER, now, "서울", [], None, row["tool_lines"])
        messages = [{"role": "system", "content": system}, *row["history"], {"role": "user", "content": row["message"]}]
        for i in range(args.repeat):
            start = time.time()
            with tracing.current_trace("reply-eval", as_type="generation", model=getattr(llm, "model", None)) as step:
                reply = llm.complete_text(messages)
                why = grade(reply, row["checks"])
                step.update(metadata={"label": args.label, "item": row["id"], "kind": row["kind"], "passed": not why})
            results.append({"id": row["id"], "kind": row["kind"], "run": i, "reply": reply, "passed": not why,
                            "why": why, "seconds": round(time.time() - start, 2)})
            print(f"{'통과' if not why else '실패'} {row['id']} #{i}: {reply[:70]!r} {why}")
    tracing.flush()

    summary = summarize(results)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps({"label": args.label, "model": getattr(llm, "model", None),
                                                        "summary": summary, "results": results},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[{args.label}] 모델 {getattr(llm, 'model', None)}")
    for kind, s in summary.items():
        print(f"  {kind}: {s['passed']}/{s['total']} 통과")


if __name__ == "__main__":
    main()
