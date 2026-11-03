# 환경변수를 한 곳에서 읽어서 settings 객체로 제공하는 곳.
# 다른 파일에서는 os.getenv를 직접 쓰지 말고 여기서 import해서 쓴다.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_STORAGE_BUCKET: str = "documents"
    CORS_ORIGINS: str = "http://localhost:3000"

    OPENAI_API_KEY: str
    KURE_API_KEY: str

    # 기본 RAG(baseline_chat) 재순위 모델 API 키. 없으면 재순위 없이 RRF 순위를 그대로 쓴다
    RERANK_API_KEY: str | None = None


settings = Settings()
