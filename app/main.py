from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.plan import router as plan_router

app = FastAPI(
    title="西安智能出行 Agent API",
    version="0.1.11",
    description="V1.11: 核心市区路线规划（新增可读行程文案层）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(plan_router)
