"""POST /ingest (file upload), GET /documents, DELETE /documents/{id}."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Path, Query, Request, Response, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.documents import EmptyDocumentError, clean_filename, store_document
from app.services.parsing import BinaryInputError

router = APIRouter()

MAX_BIGINT = 9223372036854775807


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.post("/ingest")
async def upload_document(
    request: Request, response: Response, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    data = await file.read()
    filename = clean_filename(file.filename)
    try:
        doc = await store_document(_engine(request), filename, data)
    except EmptyDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BinaryInputError as exc:
        raise HTTPException(status_code=400, detail="file looks binary, not text") from exc
    response.status_code = 200 if doc.duplicate else 201
    return {
        "id": doc.id,
        "source_id": doc.source_id,
        "filename": doc.filename,
        "chunks": doc.chunks,
        "duplicate": doc.duplicate,
    }


@router.get("/documents")
async def list_documents(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1, le=MAX_BIGINT),
) -> dict[str, Any]:
    sql = text(
        "SELECT d.id, d.filename, d.created_at, "
        "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) FROM documents d "
        "WHERE (CAST(:before AS bigint) IS NULL OR d.id < CAST(:before AS bigint)) "
        "ORDER BY d.id DESC LIMIT CAST(:n AS integer)"
    )
    async with _engine(request).connect() as conn:
        rows = (await conn.execute(sql, {"before": before_id, "n": limit})).all()
    items = [
        {"id": int(r[0]), "filename": r[1], "created_at": r[2], "chunks": int(r[3])} for r in rows
    ]
    return {"items": items, "next_before_id": items[-1]["id"] if len(items) == limit else None}


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    request: Request, document_id: int = Path(ge=1, le=MAX_BIGINT)
) -> Response:
    sql = text("DELETE FROM documents WHERE id = CAST(:id AS bigint) RETURNING id")
    async with _engine(request).begin() as conn:
        row = (await conn.execute(sql, {"id": document_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return Response(status_code=204)
