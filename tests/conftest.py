# 테스트 공통 설정. app/config.py와 app/db.py는 import 시점에 settings와 supabase 클라이언트를
# 만들기 때문에, app을 import하기 전에 더미 환경변수를 먼저 넣는다.
# 테스트는 실제 Supabase를 호출하지 않는다.
import os

os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-role-key"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)
