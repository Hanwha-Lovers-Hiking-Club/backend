# 환경변수를 한 곳에서 읽어서 settings 객체로 제공하는 곳.
# 다른 파일에서는 os.getenv를 직접 쓰지 말고 여기서 import해서 쓴다.
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_STORAGE_BUCKET: str = "documents"
    CORS_ORIGINS: str = "http://localhost:3000"
    LLM_PROVIDER: Literal["openai", "ollama", "anthropic"] = "openai"
    LLM_BASE_URL: AnyHttpUrl = "http://localhost:11434"
    ANTHROPIC_API_KEY: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None
    # LLM 공급자와 별도로 공식 KURE 임베딩 API에 사용한다.
    KURE_API_KEY: SecretStr | None = None
    EMBEDDING_MODEL: str = Field(default="kure-v3-20261016", pattern=r"^kure-v3-[0-9]{8}$")
    EMBEDDING_DIMENSIONS: int = 1024
    EMBEDDING_BATCH_SIZE: int = Field(default=8, ge=1, le=8)
    EMBEDDING_TIMEOUT_SECONDS: float = Field(default=30, gt=0)
    EXTRACT_MODEL: str = "gpt-6.1-sol"
    DRAFT_MODEL: str = "gpt-6-luna"
    VERIFY_MODEL: str | None = "gpt-6.1-sol"
    VERIFY_RULES_ENABLED: bool = True
    VERIFY_LLM_ENABLED: bool = True
    VERIFY_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    DOCUMENT_LLM_TIMEOUT_SECONDS: float = Field(default=60, gt=0)
    DOCUMENT_LLM_MAX_OUTPUT_TOKENS: int = Field(default=4096, gt=0)
    DOCUMENT_LLM_MAX_INPUT_CHARS: int = Field(default=48000, gt=0)

    @field_validator("VERIFY_RULES_ENABLED")
    @classmethod
    def require_verification_rules(cls, value: bool) -> bool:
        if not value:
            raise ValueError("MVP verification rules must remain enabled")
        return value

    @field_validator("EMBEDDING_DIMENSIONS")
    @classmethod
    def require_supported_embedding_dimensions(cls, value: int) -> int:
        if value not in (256, 512, 768, 1024, 2048):
            raise ValueError("unsupported KURE embedding dimensions")
        return value


settings = Settings()
