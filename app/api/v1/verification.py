"""Doctors and clinics: licence documents for CardiosensAI verification, and resubmitting after a rejection.

- A doctor (or a clinic's login) uploads its registration / licence certificate: PDF or JPEG, checked by
  content, at most MAX_FILES kept. CardiosensAI can't approve an account without one.
- Only the owner and CardiosensAI staff (admin + two-step sign-in) can open the files, through
  short-lived signed links bound to the viewer; permission is re-checked when the link is used.
- After a rejection or revocation the account fixes its details/files and resubmits (back to "pending").
  Editing the registration details (PUT /me/professional-profile, /me/organization) also resubmits.
"""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.api.v1.documents import SIGNATURES, _safe_filename
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.ratelimit import limiter
from app.core.security import sign_payload, verify_signed
from app.core.storage import get_storage
from app.models import ProfessionalProfile, User, VerificationFile
from app.schemas.common import CamelModel
from app.schemas.documents import FileLinkOut
from app.services import audit
from app.services.accounts import licence_file_count, organization_of, review_note, verification_status

router = APIRouter(tags=["verification"])

MAX_FILES = 3


class VerificationFileOut(CamelModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: str


class VerificationOut(CamelModel):
    status: str  # pending | verified | rejected | revoked | missing
    review_note: str | None
    files: list[VerificationFileOut]
    max_files: int
    can_resubmit: bool


def files_of(db: Session, user_id: uuid.UUID) -> list[VerificationFileOut]:
    rows = db.scalars(select(VerificationFile).where(VerificationFile.user_id == user_id).order_by(VerificationFile.uploaded_at))
    return [
        VerificationFileOut(
            id=str(f.id), filename=f.filename, content_type=f.content_type, size_bytes=f.size_bytes, uploaded_at=f.uploaded_at.isoformat()
        )
        for f in rows
    ]


def file_link(viewer: User, f: VerificationFile) -> FileLinkOut:
    s = get_settings()
    token = sign_payload({"t": "vfile", "f": str(f.id), "u": str(viewer.id)}, s.document_link_ttl_s)
    return FileLinkOut(url=f"{s.api_base_url.rstrip('/')}/v1/verification-files/{token}", expires_in_s=s.document_link_ttl_s)


def _professional(user: User) -> User:
    if user.role not in ("doctor", "clinic"):
        raise bad_request("not_a_professional_account", "Only doctor and clinic accounts are verified")
    return user


def _out(db: Session, user: User) -> VerificationOut:
    status = verification_status(db, user) or "missing"
    files = files_of(db, user.id)
    return VerificationOut(
        status=status,
        review_note=review_note(db, user) if status in ("rejected", "revoked") else None,
        files=files,
        max_files=MAX_FILES,
        can_resubmit=status in ("rejected", "revoked") and bool(files),
    )


@router.get("/me/verification", response_model=VerificationOut, summary="Doctors/clinics: verification status, reason and licence files")
def my_verification(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VerificationOut:
    return _out(db, _professional(user))


@router.post(
    "/me/verification/files",
    response_model=VerificationOut,
    status_code=201,
    summary="Upload licence / registration certificates (PDF or JPEG)",
)
async def upload_licence(
    files: Annotated[list[UploadFile], File(description="PDF or JPEG files")],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> VerificationOut:
    s = get_settings()
    _professional(user)
    limiter.hit(f"licence-upload:{user.id}", 30, 3600)
    have = licence_file_count(db, user.id)
    if not files or have + len(files) > MAX_FILES:
        raise bad_request("file_count", f"You can keep at most {MAX_FILES} licence files. Remove one first.")
    loaded: list[tuple[str, str, str, bytes]] = []
    for f in files:
        data = await f.read(s.max_document_file_bytes + 1)
        if len(data) > s.max_document_file_bytes:
            raise bad_request("file_too_large", f"{f.filename}: each file can be at most {s.max_document_file_bytes // (1024 * 1024)} MB")
        kind = next((v for sig, v in SIGNATURES.items() if data.startswith(sig)), None)
        if kind is None:
            raise bad_request("unsupported_file", f"{f.filename}: only PDF and JPEG files can be uploaded")
        loaded.append((_safe_filename(f.filename, kind[1]), kind[0], kind[1], data))
    storage = get_storage()
    for name, ctype, ext, data in loaded:
        fid = uuid.uuid4()
        key = f"verification/{user.id}/{fid}.{ext}"
        storage.put(key, data, ctype)
        db.add(
            VerificationFile(
                id=fid,
                user_id=user.id,
                filename=name,
                content_type=ctype,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
            )
        )
    audit.record(
        db,
        actor=user,
        action="verification.file_upload",
        resource_type="user",
        resource_id=user.id,
        detail=f"{len(loaded)} file(s)",
        meta=meta,
    )
    db.commit()
    return _out(db, user)


@router.delete("/me/verification/files/{file_id}", response_model=VerificationOut, summary="Remove a licence file (not while verified)")
def delete_licence(
    file_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> VerificationOut:
    _professional(user)
    f = db.get(VerificationFile, file_id)
    if f is None or f.user_id != user.id:
        raise not_found("File")
    if verification_status(db, user) == "verified":
        raise conflict("verified", "Your account is verified with these documents. Upload a newer file instead of removing this one.")
    key = f.storage_key
    db.delete(f)
    audit.record(db, actor=user, action="verification.file_delete", resource_type="user", resource_id=user.id, meta=meta)
    db.commit()
    get_storage().delete(key)
    return _out(db, user)


@router.post("/me/verification/files/{file_id}/link", response_model=FileLinkOut, summary="Short-lived link to view your licence file")
def my_licence_link(file_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileLinkOut:
    f = db.get(VerificationFile, file_id)
    if f is None or f.user_id != user.id:
        raise not_found("File")
    return file_link(user, f)


@router.post(
    "/me/verification/resubmit", response_model=VerificationOut, summary="After a rejection or revocation: ask CardiosensAI to review again"
)
def resubmit(
    user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> VerificationOut:
    _professional(user)
    status = verification_status(db, user)
    if status not in ("rejected", "revoked"):
        raise conflict("not_rejected", "Only a rejected or revoked account can be resubmitted")
    if licence_file_count(db, user.id) == 0:
        raise bad_request("licence_required", "Upload your registration / licence certificate first")
    # The status above came from this row, so it exists.
    row = db.get(ProfessionalProfile, user.id) if user.role == "doctor" else organization_of(db, user)
    row.verification_status, row.review_note, row.verified_at, row.verified_by = "pending", None, None, None  # type: ignore[union-attr]
    if isinstance(row, ProfessionalProfile):
        row.submitted_at = utcnow()
    audit.record(db, actor=user, action="verification.resubmit", resource_type="user", resource_id=user.id, meta=meta)
    db.commit()
    return _out(db, user)


@router.get(
    "/verification-files/{token}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/jpeg": {}}}},
    summary="Open a licence file through its signed link (owner or CardiosensAI staff)",
)
def open_licence(token: str, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> Response:
    gone = ApiError(410, "link_expired", "This link has expired. Open the file again.")
    body = verify_signed(token)
    if body is None or body.get("t") != "vfile":
        raise gone
    try:
        f = db.get(VerificationFile, uuid.UUID(body["f"]))
        viewer = db.get(User, uuid.UUID(body["u"]))
    except (KeyError, ValueError) as exc:
        raise gone from exc
    if f is None or viewer is None or viewer.status != "active":
        raise gone
    staff = viewer.role == "admin" and viewer.mfa_enabled
    if not staff and viewer.id != f.user_id:
        raise forbidden()
    data = get_storage().get(f.storage_key)
    if hashlib.sha256(data).hexdigest() != f.sha256:
        raise ApiError(500, "integrity_error", "Stored file failed its integrity check")
    if staff:
        audit.record(
            db, actor=viewer, action="admin.licence_view", resource_type="user", resource_id=f.user_id, detail=f.filename, meta=meta
        )
        db.commit()
    ascii_name = f.filename.encode("ascii", "replace").decode().replace('"', "")
    headers = {
        "Content-Disposition": f'inline; filename="{ascii_name}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if f.content_type == "image/jpeg":
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return Response(content=data, media_type=f.content_type, headers=headers)
