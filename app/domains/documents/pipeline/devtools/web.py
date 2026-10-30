# langgraph.json에 연결하는 로컬 개발 화면과 확인용 API를 제공한다. 제품 백엔드 API와는 별개다.

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.domains.documents.pipeline.devtools.demo import get_demo_graph
from app.domains.documents.pipeline.graph import get_graph

app = FastAPI(
    title="Document pipeline local viewer", docs_url=None, redoc_url=None, openapi_url=None
)


class EdgeView(BaseModel):
    source: str
    target: str
    conditional: bool


class GraphView(BaseModel):
    nodes: list[str]
    edges: list[EdgeView]


class DemoRun(BaseModel):
    is_demo: bool = True
    visited_nodes: list[str]
    decisions: list[str]
    admitted_count: int
    review_count: int
    retry_count: int
    saved_count: int = 0


@app.get("/dev", response_model=None, response_class=FileResponse)
async def viewer():
    # HTML file response, not a JSON product API contract.
    return FileResponse(Path(__file__).with_name("viewer.html"), media_type="text/html")


@app.get("/dev/graph", response_model=GraphView)
async def graph_view():
    graph = get_graph().get_graph()
    return GraphView(
        nodes=list(graph.nodes),
        edges=[
            EdgeView(
                source=edge.source,
                target=edge.target,
                conditional=edge.conditional,
            )
            for edge in graph.edges
        ],
    )


@app.post("/dev/demo", response_model=DemoRun)
async def run_demo():
    visited, final = [], {}
    async for update in get_demo_graph().astream({}, stream_mode="updates"):
        visited.extend(update)
        if "finish" in update:
            final = update["finish"]
    if not final:
        raise RuntimeError("Demo graph did not reach finish")
    return DemoRun(
        visited_nodes=visited,
        decisions=[item.decision for item in final["verification_results"]],
        admitted_count=len(final["verified_drafts"]),
        review_count=len(final["review_drafts"]),
        retry_count=len(final.get("verification_history", [])),
    )
