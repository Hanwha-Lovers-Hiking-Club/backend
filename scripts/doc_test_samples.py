"""실제 문서 테스트 샘플 목록 (한화사랑 데이터셋).

규칙: 프로젝트를 절대 섞지 않는다. 파이프라인은 job 하나의 모든 문서를 한꺼번에 보고 "같은 경험"을
묶으므로, 서로 다른 프로젝트 문서가 한 job에 들어가면 무관한 경험이 한 카드로 합쳐질 수 있다.
그래서 2~3단계는 프로젝트 하나 = job 하나로 돌리고, 같은 문서의 다른 형식(md/pdf)도 따로 돌린다.

경로는 데이터셋 `<프로젝트>-*-dataset/original_markdown/` 아래의 상대 경로이고, 끝부분만 써도 된다
(프로젝트 안에서 하나로 정해져야 한다). docx/pdf는 같은 경로에서 확장자만 바꿔 찾는다.

core: 2~3단계(LLM 호출)까지 가져갈 문서. 프로젝트마다 "경험이 서술된 문서" 위주로 2개.
edge: 1단계(변환만, 비용 0)에서만 보는 문서. 이미지 포함, 아주 긴 문서, PDF 텍스트
      추출률이 낮게 나온 문서처럼 변환이 깨질 만한 것들이다.
"""

SAMPLES: dict[str, dict[str, list[tuple[str, str]]]] = {
    "3jmt": {
        "core": [
            (
                "docs/records/trade-offs/settlement/order-data-sourcing.md",
                "trade-off 기록, 표·코드",
            ),
            (
                "docs/records/troubleshooting/infra/kubernetes-cd-rollout-and-apply-conflicts.md",
                "트러블슈팅, 코드블록 16개",
            ),
        ],
        "edge": [
            (
                "adr-0003-spring-boot-4-flyway-autoconfigure-modularization.md",
                "이미지 1개 포함",
            ),
            (
                "2026-07-18-payment-failure-compensation-test-plan.md",
                "24쪽, 표 6개, PDF 추출률 낮음(0.78)",
            ),
        ],
    },
    "mechadog": {
        "core": [
            ("docs/case-studies/02-person-gate-time-window.md", "사례 연구, 표·코드"),
            ("docs/case-studies/05-ppe-domain-gap.md", "사례 연구, 표 3개"),
        ],
        "edge": [
            ("README.md", "이미지 3개 포함"),
            (
                "field_tests/results/20261006_ppe-xiao/summary.md",
                "516KB, 157쪽, PDF 추출률 최저권(0.61) — 변환이 매우 느릴 수 있음",
            ),
        ],
    },
    "schale": {
        "core": [
            ("docs/troubleshooting.md", "한 문서에 문제-해결이 여러 개(20쪽), 코드 17개"),
            ("docs/15_adr_007_transactional_outbox.md", "ADR, 표 3개"),
        ],
        "edge": [("README.md", "이미지 7개 포함")],
    },
    "ssafesta": {
        "core": [
            ("docs/LJH/verify/artifact-routing-incidents.md", "장애 기록, 표 5개"),
            ("docs/PJW/verify/263-perf-fix.md", "성능 개선 기록"),
        ],
        "edge": [
            ("docs/KHS/measurements/build-report-audit.md", "PDF 추출률 0.32로 전체 최저"),
        ],
    },
    "token": {
        "core": [
            ("docs/adr/0001-menubar-rendering.md", "ADR, 짧음"),
            ("docs/perf.md", "성능 측정, 표 13개"),
        ],
        "edge": [("README.md", "이미지 8개 포함")],
    },
    "vigilantis": {
        "core": [
            ("docs/adr/0004-rollback-runbook-whitelist-registration.md", "ADR"),
            ("docs/adr/0010-platform-deployment-saas-shape.md", "ADR, 12.9KB"),
        ],
        "edge": [("docs/AWS_SMOKE_RESULT.md", "24쪽, 표 21개, PDF 추출률 낮음(0.70)")],
    },
}
