# 프론트엔드 컨테이너와 docker-compose로 같이 띄우기 위한 이미지.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 문서 변환 모델을 이미지에 미리 받아 둔다. 레이아웃 모델은 ~/.cache/huggingface, OCR 파일은
# .venv 안으로 들어가 경로가 둘이라서 볼륨 대신 이미지에 넣는다.
# 앱 코드를 복사하기 전에 받아서 코드만 바뀌면 이 단계는 빌드 캐시를 쓴다
COPY scripts/download_models.py ./
RUN .venv/bin/python download_models.py

COPY app ./app

EXPOSE 8000

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
