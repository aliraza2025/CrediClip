from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from app.models import AnalyzeRequest, AnalyzeResponse
from app.services.pipeline import analyze_video

load_dotenv()

app = FastAPI(title="CrediClip MVP", version="0.1.0")

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    try:
        return await analyze_video(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
