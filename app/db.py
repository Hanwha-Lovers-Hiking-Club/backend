# supabase 클라이언트를 앱 전체에서 하나만 만들어서 공유하는 곳.
# 다른 파일에서는 `from app.db import supabase` 로 가져다 쓰기만 하면 된다.
#
# ponytail: 동기(Client) 클라이언트라 DB 호출 중 이벤트 루프가 블로킹됨. 해커톤 트래픽 규모에선
# 문제 없을 거라 의도적으로 동기로 둠. 동시 요청이 실제로 밀리기 시작하면 create_async_client로
# 바꾸고(이건 코루틴이라 FastAPI lifespan에서 생성 필요), 이 모듈의 `from app.db import supabase`
# 패턴도 같이 손봐야 함.
from supabase import Client, create_client

from app.config import settings

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
