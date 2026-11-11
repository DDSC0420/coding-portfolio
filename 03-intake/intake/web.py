"""The local web page: drop a CV, see the record and where each value came from.

Uploads are processed in memory and never written to disk or logged.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .llm import LlmExtractor
from .pipeline import extract
from .readers import MAX_BYTES, ReadError

STATIC = Path(__file__).resolve().parent / "static"
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_LABELS = {
    "amara-osei.pdf": "Amara · PDF",
    "tom-brennan.docx": "Tom · Word",
    "sofia-marlowe.txt": "Sofia · text",
    "rowan-hale.txt": "Rowan · messy text",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                                "form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def create_app(llm: LlmExtractor | None = None, today: Callable[[], date] = date.today) -> FastAPI:
    app = FastAPI(title="Intake", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def run(data: bytes, filename: str) -> JSONResponse:
        try:
            result = extract(data, filename, llm=llm, today=today())
        except ReadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return JSONResponse(result.model_dump(mode="json"))

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        return {
            "extractor": llm.label if llm else "rules",
            "max_bytes": MAX_BYTES,
            "samples": [{"name": n, "label": label} for n, label in SAMPLE_LABELS.items() if (SAMPLES / n).exists()],
        }

    @app.post("/api/extract")
    def extract_upload(request: Request, file: Annotated[UploadFile, File()]) -> JSONResponse:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES + 64 * 1024:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        data = file.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        return run(data, Path(file.filename or "upload").name)

    @app.post("/api/samples/{name}")
    def extract_sample(name: str) -> JSONResponse:
        if name not in SAMPLE_LABELS:  # a fixed list, so no path tricks
            raise HTTPException(status_code=404, detail="No such sample.")
        return run((SAMPLES / name).read_bytes(), name)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
