# 여러 도메인이 같이 쓰는 pydantic 베이스 모델을 두는 곳.
# API 명세서는 JSON 키를 camelCase로 정했고, 파이썬 코드는 snake_case 규칙이라
# 필드는 snake_case로 선언하고 요청/응답 JSON에서만 camelCase로 바꿔준다.
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
    )
