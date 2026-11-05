# 챗봇 원문 검색용 인덱싱 경로(dev에서 옮김): parse -> chunk -> embed_chunks -> save_chunks.
# 카드 생성 파이프라인(graph.py)과 독립이다. 카드 경로가 실패해도 이 경로는 따로 돌고,
# 이 경로가 실패해도 작업은 실패하지 않는다(warnings에만 남는다).
# 검색용 청크는 카드 생성용 청크(pipeline/chunking.py)와 크기·위치 형식이 달라 재사용하지 않는다.
