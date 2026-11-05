# 신규 Supabase 프로젝트 준비

현재 HYUNGU 문서 업로드 → 지식카드 생성 → 저장 코드에 맞춘 초기화 안내다.
기존 DB의 데이터·Storage 원본을 복사하는 작업은 포함하지 않는다.

## 1. SQL 실행

팀장이 **새 프로젝트**의 SQL Editor에서
[2026-11-05_new_supabase_pipeline.sql](2026-11-05_new_supabase_pipeline.sql)
전체를 한 번에 실행한다. 파이프라인 테이블이 없는 프로젝트용이다.
대상 테이블이 하나라도 있으면 중단하며 기존 테이블을 지우지 않는다.
전체 SQL이 성공한 뒤 재실행하면 같은 사전 검사에서 중단하는 것이 정상이다.

| 생성 대상 | 역할 |
|---|---|
| documents | 원본 파일명·용량·Storage 위치 |
| knowledge_cards | 카드 본문·상태·4개 관점의 1,024차원 벡터 |
| card_documents | 카드와 근거 문서·위치·인용 연결 |
| document_save_receipts | 문서 단위 저장 확정·동일 요청의 중복 저장 방지 |
| document_jobs | 업로드 후 실행 작업과 최종 결과 |
| document_job_events | 진행 이벤트·SSE 재접속 이력 |
| save_document_cards | 카드·근거·벡터·영수증을 한 트랜잭션으로 저장 |
| create_document_job | 작업·최초 이벤트 등록 |
| record_document_job_event | 작업 상태·이벤트 기록 |
| expire_stale_document_job | 오래 갱신되지 않은 작업 종료 |

테이블·함수 정의는 [tables.sql](tables.sql), [functions.sql](functions.sql)에서
가져온 스냅샷이며, 컬럼과 API 계약은 변경하지 않았다. 원본 변경 시 배포용 파일도
다시 동기화해야 한다. 파일에 원본별 SHA-256(UTF-8/LF)을 기록했다.
테이블은 모두 RLS 활성화·사용자 정책 없음, 백엔드 service_role 접근 방식이다.
새 프로젝트의 기본 grants에 의존하지 않도록 백엔드 테이블 권한도 명시했다.
검색 담당자와 확정하지 않은 검색 RPC·벡터 인덱스는 추가하지 않았다.

## 2. Storage 준비

Supabase Dashboard → Storage에서 `documents` 버킷을 **Private**로 만든다.
현재 `SUPABASE_STORAGE_BUCKET`의 기본값과 일치하는 이름이다.
별도 이름을 사용하면 해당 설정도 같은 값으로 맞춘다.
현재 업로드 API의 파일당 한도는 5 MiB이므로 버킷의 파일 크기 제한은
최소 5 MiB 이상으로 설정한다. 전체 10 MiB 제한은 업로드 API가 검사한다.
확장자와 MIME 표기가 달라 TXT·MD 업로드가 막히지 않도록 MIME 제한을 설정할 때 주의한다.

백엔드는 service role로 Storage에 접근한다. 브라우저용 공개 읽기/쓰기 정책은 추가하지 않는다.
버킷 생성은 [Supabase 공식 안내](https://supabase.com/docs/guides/storage/buckets/creating-buckets),
권한은 [Storage Access Control](https://supabase.com/docs/guides/storage/security/access-control)을 따른다.
SQL 파일은 Storage 시스템 테이블을 수정하지 않는다.

## 3. 연결 설정 변경

```dotenv
SUPABASE_URL=https://<새-프로젝트-ref>.supabase.co
SUPABASE_STORAGE_BUCKET=documents
EMBEDDING_DIMENSIONS=1024
```

`SUPABASE_SERVICE_ROLE_KEY`도 **같은 새 프로젝트**에서 발급한 값으로 교체한다.
기존 Windows 사용자 환경변수로 관리 중이면 그 값을 바꾼다.
실제 키는 SQL·문서·Git에 넣지 않는다. 현재 설정은 환경변수가 `.env`보다 우선하므로
이전 프로젝트의 URL이나 키가 환경변수에 남아 있지 않은지도 확인한다.
변경 후 VS Code/터미널과 서버를 재시작해 새 환경변수를 읽게 한다.
OpenAI/KURE 설정은 DB 변경만을 이유로 바꿀 필요가 없다.
이번 SQL 작성 작업에서는 실제 설정 파일·환경변수를 수정하지 않았다.

## 4. 확인과 한계

- SQL 끝의 조회에서 테이블 6개/RLS true와 RPC 4개를 확인한다.
- 실제 저장·동일 요청 재호출·롤백·0건 저장 검사는 새 테스트 프로젝트에서
  [smoke_document_save.py](../scripts/smoke_document_save.py)로 별도 수행한다.
  임시 active 카드를 생성하는 검사이므로 라이브 DB에서는 실행하지 않는다.
- 현재 파일은 새 DB에 직접 실행해 검증한 결과물이 아니다. 구문 검사와 기존
  SQL·호출 코드와의 일치 여부를 확인한 배포용 SQL이다.
- `needs_review` 카드와 필드별 사실 상태의 중앙 DB 저장은 기존 구현과 동일하게
  미완료다. 이 SQL은 별도 보류 카드 테이블이나 자동 승인 기능을 추가하지 않는다.
- 기존 DB의 문서·카드·작업 ID와 Storage 파일은 자동 이전되지 않는다.
  기존 작업은 SQLite 체크포인트에도 남을 수 있으므로 DB 전환 후 새 업로드·새 작업으로 확인한다.
