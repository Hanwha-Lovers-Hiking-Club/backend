-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- knowledge_cards에 지식카드 검색용 임베딩 4종을 추가한다 (임베딩 설계 가이드 참고).
-- 차원은 1024: kure-v3(256~2048 지원)는 차원을 낮춰도 정확도 손실이 크지 않고,
-- pgvector는 2,000차원을 넘으면 HNSW/IVFFlat 인덱스를 못 걸어서 그 밑으로 여유 있게 잡았다.
-- 인덱스는 아직 안 만든다 — 카드 수/조회 패턴이 쌓인 뒤에 필요해지면 추가한다.
-- 여러 번 실행해도 안전하다.

create extension if not exists vector;

alter table knowledge_cards add column if not exists content_embedding vector(1024);
alter table knowledge_cards add column if not exists situation_embedding vector(1024);
alter table knowledge_cards add column if not exists solution_embedding vector(1024);
alter table knowledge_cards add column if not exists lesson_embedding vector(1024);
