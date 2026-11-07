#!/usr/bin/env bash
# EC2에서 main 최신 코드를 받아 다시 띄운다. GitHub Actions의 배포 전용 키가 이 스크립트만 실행한다.
# git reset이 실행 중인 이 파일을 바꿀 수 있어서, 함수로 묶어 전체를 읽은 뒤에 실행한다
set -euo pipefail

main() {
    local compose=(docker compose -f docker-compose.aws.yml)

    cd "$HOME/app"
    # 배포가 겹치면 빌드와 컨테이너 교체가 꼬이므로 하나씩만 실행한다
    exec 9>"$HOME/.deploy.lock"
    flock 9

    git fetch --quiet origin main
    git reset --quiet --hard origin/main
    echo "배포 커밋: $(git log -1 --format='%h %s')"

    # 빌드하는 동안에는 기존 컨테이너가 계속 돌고, 교체할 때만 잠깐 끊긴다.
    # 재시작하면 분석 중인 문서 작업은 사라진다 (documents/service.py의 _start_pipeline 참고)
    "${compose[@]}" up -d --build
    # Caddyfile은 파일 마운트라 컨테이너가 안 바뀌어도 설정을 다시 읽어야 한다
    "${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
    # docker image prune는 넣지 않는다. 이미지를 지우면 빌드 캐시도 깨져서 매번 uv sync부터 다시 돈다

    local attempt
    for attempt in {1..30}; do
        if curl -fsS -m 3 -o /dev/null https://job-knowledge.duckdns.org/health; then
            echo "배포 완료: ${attempt}번째 확인에서 /health 정상"
            return 0
        fi
        sleep 2
    done
    echo "배포 실패: /health가 응답하지 않는다" >&2
    "${compose[@]}" ps >&2
    return 1
}

main
exit
