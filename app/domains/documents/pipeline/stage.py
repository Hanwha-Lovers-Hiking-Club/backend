# 항목 단위로 처리하는 노드(draft_card, verify, embed)의 공통 도구: 병렬 실행과 마무리.
#
# 항목(경험, 카드)은 서로 독립적이라 하나가 실패해도 나머지는 계속 처리한다. 실패한 항목은 건너뛰고
# state["warnings"]에 남기며, 처리하려던 항목이 전부 실패했을 때만 error로 job을 실패시킨다.
# 처리할 항목이 처음부터 없는 것은 정상이다(failures가 비어 있으면 error가 아니다).
import contextvars
from concurrent.futures import ThreadPoolExecutor

from app.domains.documents.pipeline.state import IngestionState


def run_parallel(fn, items: list, max_workers: int) -> list:
    """items마다 fn(item)을 스레드로 동시에 돌리고, 입력 순서대로 결과 또는 그때 난 예외를 돌려준다.

    항목끼리 독립인 LLM 호출용이다. 예외를 던지지 않고 값으로 돌려줘서, 호출하는 쪽이 항목마다
    건너뛸지 정한다.
    """
    # 스레드에는 contextvar(job_id 로그·사용량 집계)가 넘어가지 않아서 호출마다 복사해 넘긴다
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
    results: list = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as e:
            results.append(e)
    return results


def finish_stage(
    state: IngestionState,
    key: str,
    items: list,
    failures: list[str],
    succeeded: int | None = None,
) -> dict:
    # succeeded: 실제 호출이 성공한 항목 수. 기본은 len(items)이고,
    # 호출 없이 만들어지는 결과(예: 구조 검사 탈락)가 items에 섞여 있으면 따로 넘긴다
    if failures and (len(items) if succeeded is None else succeeded) == 0:
        return {"error": f"{failures[0]} (처리하려던 {len(failures)}개 모두 실패)"}
    update: dict = {key: items}
    if failures:
        update["warnings"] = [*state.get("warnings", []), *failures]
    return update
