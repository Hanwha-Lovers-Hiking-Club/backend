# 기본 RAG 챗봇 로직: retrieve -> generate 노드를 순서대로 호출하고 SSE 이벤트로 흘려보낸다.
# 토큰 스트리밍 때문에 LangGraph 없이 이 파일이 노드 호출 순서를 직접 관리한다.
