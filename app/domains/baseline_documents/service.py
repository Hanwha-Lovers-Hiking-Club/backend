# 기본 RAG 문서 도메인의 진입점 로직.
# 이미 업로드된 문서를 id로 받아 파이프라인을 실행한다.
# 순서: parse -> chunk -> contextualize -> embed -> save
