"""Community submissions and moderation (bearer JWT from the web platform)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal
from ..models import Contributor, Submission
from ..submissions import create_submission, review_submission
from ..validation import SubmissionError
from . import queries as Q
from . import schemas as S
from .deps import ApiError, Paging, paging, principal, session

router = APIRouter(prefix="/v1", tags=["submissions"])

SUBMISSION_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {"schema": {
                "type": "object", "required": ["file", "metadata"],
                "properties": {
                    "file": {"type": "string", "format": "binary",
                             "description": "CSV/TSV (wavelength + n,k or eps1,eps2 columns) or "
                                            "refractiveindex.info-style YAML"},
                    "metadata": {"type": "string",
                                 "description": "JSON object with the SubmissionInput fields except 'data', plus "
                                                "'wavelength_unit' (nm, um, eV, cm-1) and optional 'columns'"},
                }}},
            "application/json": {"schema": {"$ref": "#/components/schemas/SubmissionInput"}},
        },
    }
}


def _person(c: Contributor | None) -> dict | None:
    return {"sub": c.external_id, "name": c.name, "role": c.role} if c else None


def submission_out(sub: Submission, with_data: bool = True) -> dict:
    meta = sub.meta or {}
    stats = (sub.validation or {}).get("stats", {})
    out = {
        "id": sub.public_id, "status": sub.status, "kind": sub.kind,
        "material_id": sub.material.public_id if sub.material else None,
        "material": Q.material_ref(sub.material) if sub.material else None,
        "proposed_material": sub.proposed_material,
        "target_dataset_id": sub.target_dataset.public_id if sub.target_dataset else None,
        "material_name": meta.get("material_name") or (sub.material.name if sub.material else None),
        "category": meta.get("category") or (sub.material.category if sub.material else None),
        "dataset_name": meta.get("dataset_name") or meta.get("name"),
        "reference": meta.get("reference") or meta.get("citation") or (sub.reference.citation if sub.reference
                                                                       else None),
        "doi": sub.reference.doi if sub.reference else meta.get("doi"),
        "license": sub.license_id, "comments": meta.get("comments"), "metadata": meta,
        "validation": sub.validation or {},
        "reference_info": Q.reference_out(sub.reference) if sub.reference else None,
        "submitted_by": _person(sub.contributor), "reviewer": _person(sub.reviewer),
        "review_note": sub.moderation_notes,
        "result_dataset_id": sub.result_dataset.public_id if sub.result_dataset else None,
        "result_dataset_slug": sub.result_dataset.slug if sub.result_dataset else None,
        "file": {"filename": sub.source_file.filename, "size": sub.source_file.size,
                 "sha256": sub.source_file.sha256, "media_type": sub.source_file.media_type},
        "data_summary": {"type": stats.get("data_type"), "range_nm": stats.get("range_nm"),
                         "points": stats.get("points"), "has_k": stats.get("has_k")},
        "events": [{"action": e.action, "actor": _person(e.actor), "notes": e.notes, "created_at": e.created_at}
                   for e in sub.events],
        "created_at": sub.created_at, "reviewed_at": sub.reviewed_at,
    }
    if with_data:
        rows = []
        for b in sub.blocks or []:
            if "wavelength_um" in b:
                wl, n, k = b["wavelength_um"], b.get("n"), b.get("k")
                rows = [[w * 1000.0, n[i] if n else None, k[i] if k else None] for i, w in enumerate(wl)]
                break
        out["data"] = rows
    return out


@router.post("/submissions", response_model=S.SubmissionOut, status_code=201, openapi_extra=SUBMISSION_BODY,
             responses={401: {"model": S.Error}, 422: {"model": S.Error}},
             summary="Submit a dataset (multipart file + metadata, or JSON with inline rows)")
async def submit(request: Request, who: Principal = Depends(principal), s: Session = Depends(session)):
    cfg = request.app.state.settings
    ctype = request.headers.get("content-type", "")
    rows = None
    if ctype.startswith("application/json"):
        raw = await request.body()
        if len(raw) > cfg.max_upload_bytes:
            raise ApiError(413, "too_large", f"request larger than {cfg.max_upload_bytes} bytes")
        try:
            body = S.SubmissionInput.model_validate_json(raw)
        except ValidationError as e:
            raise ApiError(422, "invalid_body", "invalid submission JSON", json.loads(e.json())) from None
        meta = body.model_dump(exclude={"data"}, exclude_none=True)
        rows = body.data
        content = raw
        filename, media = "submission.json", "application/json"
    elif ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ApiError(422, "missing_file", "multipart field 'file' is required")
        content = await upload.read()
        if len(content) > cfg.max_upload_bytes:
            raise ApiError(413, "too_large", f"file larger than {cfg.max_upload_bytes} bytes")
        try:
            meta = json.loads(form.get("metadata") or "{}")
            if not isinstance(meta, dict):
                raise ValueError("metadata must be a JSON object")
        except ValueError as e:
            raise ApiError(422, "invalid_metadata", f"'metadata' must be a JSON object: {e}") from None
        filename = upload.filename or "upload"
        name = filename.lower()
        media = ("application/yaml" if name.endswith((".yml", ".yaml")) else "text/csv")
    else:
        raise ApiError(415, "unsupported_media_type", "send multipart/form-data (file + metadata) or JSON")
    try:
        sub = create_submission(s, who, meta, content=content, filename=filename, media_type=media, rows=rows,
                                resolver=request.app.state.resolver,
                                resolve_material=lambda x: Q.get_material(s, x),
                                resolve_dataset=lambda x: Q.get_dataset(s, x))
    except SubmissionError as e:
        raise ApiError(422, "validation_failed", str(e), {"validation": e.report}) from None
    return submission_out(sub)


@router.get("/submissions", response_model=S.Page[S.SubmissionOut],
            summary="Submissions: moderators see all, others their own")
def list_submissions(status: str | None = Query(None, description="pending | approved | rejected"),
                     mine: bool = Query(False, description="Only my submissions (moderators)"),
                     include_data: bool = False,
                     who: Principal = Depends(principal), p: Paging = Depends(paging),
                     s: Session = Depends(session)):
    stmt = select(Submission)
    if not who.is_moderator or mine:
        stmt = stmt.join(Contributor, Contributor.id == Submission.contributor_id).where(
            Contributor.external_id == who.sub)
    if status:
        stmt = stmt.where(Submission.status == status)
    total = s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = s.scalars(stmt.order_by(Submission.created_at.desc(), Submission.id.desc())
                     .limit(p.limit).offset(p.offset)).all()
    return p.envelope([submission_out(x, with_data=include_data) for x in rows], total)


def _load(s: Session, sid: str, who: Principal) -> Submission:
    sub = s.scalar(select(Submission).where(Submission.public_id == sid))
    if sub is None or (not who.is_moderator and sub.contributor.external_id != who.sub):
        raise ApiError(404, "not_found", f"no submission {sid!r}")
    return sub


@router.get("/submissions/{submission_id}", response_model=S.SubmissionOut, summary="One submission")
def get_submission(submission_id: str, who: Principal = Depends(principal), s: Session = Depends(session)):
    return submission_out(_load(s, submission_id, who))


@router.get("/submissions/{submission_id}/file", summary="The uploaded file, verbatim")
def submission_file(submission_id: str, who: Principal = Depends(principal), s: Session = Depends(session)):
    sf = _load(s, submission_id, who).source_file
    return Response(sf.content, media_type=sf.media_type,
                    headers={"Content-Disposition": f'attachment; filename="{sf.filename}"'})


@router.post("/submissions/{submission_id}/review", response_model=S.SubmissionOut,
             responses={403: {"model": S.Error}, 409: {"model": S.Error}},
             summary="Approve (publishes the dataset) or reject; moderators only")
def review(submission_id: str, body: S.ReviewInput, who: Principal = Depends(principal),
           s: Session = Depends(session)):
    if not who.is_moderator:
        raise ApiError(403, "forbidden", "moderator role required")
    sub = _load(s, submission_id, who)
    try:
        review_submission(s, sub, who, body.decision, body.notes or body.note)
    except ValueError as e:
        raise ApiError(409, "conflict", str(e)) from None
    return submission_out(sub)
