# supabase 클라이언트를 앱 전체에서 하나만 만들어서 공유하는 곳.
# 다른 파일에서는 `from app.db import supabase` 로 가져다 쓰기만 하면 된다.
from supabase import Client, create_client

from app.config import settings

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
