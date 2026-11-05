# supabase 클라이언트를 앱 전체에서 하나만 만들어서 공유하는 곳.
# 다른 파일에서는 `from app.db import supabase` 로 가져다 쓰기만 하면 된다.
#
# ponytail: 동기(Client) 클라이언트라 DB 호출 중 이벤트 루프가 블로킹됨. 해커톤 트래픽 규모에선
# 문제 없을 거라 의도적으로 동기로 둠. 동시 요청이 실제로 밀리기 시작하면 create_async_client로
# 바꾸고(이건 코루틴이라 FastAPI lifespan에서 생성 필요), 이 모듈의 `from app.db import supabase`
# 패턴도 같이 손봐야 함.
#
# HTTP/2를 끄고 HTTP/1.1을 쓰는 이유: 이 클라이언트는 기본으로 HTTP/2 연결 하나를 여러 요청이 나눠
# 쓰는데, 동기 HTTP/2 연결은 여러 스레드(asyncio.to_thread)가 동시에 쓰면 끊긴다
# (httpx.RemoteProtocolError: Server disconnected). 챗봇 검색을 동시 4명으로 돌리면 20%, 8명이면
# 55%가 실패했고, HTTP/1.1로는 16명까지 실패가 없었다(2026-11-04 측정). HTTP/1.1은 요청마다 연결을
# 따로 써서 이 문제가 없다.
# httpx_client를 넘기면 DB(postgrest)와 Storage가 이 클라이언트를 같이 쓰고, ClientOptions의
# postgrest/storage 타임아웃은 무시된다. 그래서 타임아웃을 여기서 정한다 (기존 DB 기본값 120초.
# Storage 기본값은 20초였는데 큰 파일 업로드에는 길수록 안전하다)
import httpx
from supabase import Client, ClientOptions, create_client

from app.config import settings

HTTP_TIMEOUT_SECONDS = 120

supabase: Client = create_client(
    settings.SUPABASE_URL,
    settings.SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(httpx_client=httpx.Client(http2=False, timeout=HTTP_TIMEOUT_SECONDS)),
)
