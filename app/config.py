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

    # 챗봇이 검색한 지식카드를 독립 검증 에이전트로 교차 검증할지 여부 (답변 지연/LLM 비용이 늘어남)
    CHAT_VERIFY_ENABLED: bool = True


settings = Settings()
