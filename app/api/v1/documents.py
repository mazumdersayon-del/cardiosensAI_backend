"""Medical documents: PDFs and JPEGs uploaded by the patient or a linked doctor/clinic, with doctors' comments.

Who can do what (the patient's consent is the access grant created when they approved a link):
- See and download: the patient; doctors and clinics with an active link to the patient. Family
  members with a vitals share don't see documents.
- Upload: the patient; linked doctors and clinics. Several files per document, PDF or JPEG only,
  checked by content (not by name or the browser's type).
- Comment: linked doctors. Everyone who can see the document sees the comments.
- Delete a document: the patient (it's their record) or whoever uploaded it. Delete a comment: its author.
Files are downloaded through short-lived signed links bound to one user; permission is re-checked on use.
"""

import hashlib
import re
import unicodedata
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import RequestMeta, get_current_user, request_meta
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.ratelimit import limiter
from app.core.security import sign_payload, verify_signed
from app.core.storage import get_storage
from app.models import Document, DocumentComment, DocumentFile, Organization, User
from app.schemas.documents import CATEGORIES, CommentIn, CommentOut, DocumentFileOut, DocumentOut, FileLinkOut
from app.services import audit
from app.services.authz import AccessContext, get_access, resolve_patient_id
from app.services.notifications import notify

router = APIRouter(tags=["documents"])

SIGNATURES = {b"%PDF-": ("application/pdf", "pdf"), b"\xff\xd8\xff": ("image/jpeg", "jpg")}


# ---------------------------------------------------------------- access


def _access(db: Session, user: User, patient_id: uuid.UUID, *, download: bool = False) -> AccessContext:
    ctx = get_access(db, user, patient_id, for_download=download)
    if ctx.via == "grant" and (ctx.grant is None or ctx.grant.role not in ("doctor", "clinic")):
        raise not_found("Patient")  # family shares cover vitals only
    return ctx


def _doc(db: Session, user: User, document_id: uuid.UUID, *, download: bool = False) -> tuple[Document, AccessContext]:
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise not_found("Document")
    return doc, _access(db, user, doc.patient_id, download=download)


def _can_comment(user: User, ctx: AccessContext) -> bool:
    return user.role == "doctor" and ctx.via == "grant"


def _display_name(db: Session, u: User | None) -> str | None:
    if u is None:
        return None
    if u.role == "clinic" and u.organization_id:
        org = db.get(Organization, u.organization_id)
        return org.name if org else u.full_name
    return u.full_name


def _out(db: Session, user: User, ctx: AccessContext, doc: Document) -> DocumentOut:
    files = db.scalars(select(DocumentFile).where(DocumentFile.document_id == doc.id).order_by(DocumentFile.position))
    comments = db.scalars(
        select(DocumentComment)
        .where(DocumentComment.document_id == doc.id, DocumentComment.deleted_at.is_(None))
        .order_by(DocumentComment.created_at)
    )
    uploader = db.get(User, doc.uploaded_by) if doc.uploaded_by else None
    out_comments = []
    for c in comments:
        author = db.get(User, c.author_id) if c.author_id else None
        out_comments.append(
            CommentOut(
                id=str(c.id),
                author_name=_display_name(db, author),
                author_role=author.role if author else None,
                body=c.body,
                created_at=c.created_at,
                mine=c.author_id == user.id,
            )
        )
    return DocumentOut(
        id=str(doc.id),
        patient_id=str(doc.patient_id),
        title=doc.title,
        category=doc.category,
        document_date=doc.document_date,
        note=doc.note,
        uploader_name=_display_name(db, uploader),
        uploader_role=doc.uploader_role,
        created_at=doc.created_at,
        files=[DocumentFileOut(id=str(f.id), filename=f.filename, content_type=f.content_type, size_bytes=f.size_bytes) for f in files],
        comments=out_comments,
        can_delete=ctx.via == "self" or doc.uploaded_by == user.id,
        can_comment=_can_comment(user, ctx),
    )


def _safe_filename(name: str | None, ext: str) -> str:
    base = unicodedata.normalize("NFKC", (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    base = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", base).strip(" .") or f"file.{ext}"
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem[:180]}.{ext}"


# ---------------------------------------------------------------- endpoints


@router.post(
    "/patients/{patient_id}/documents",
    response_model=DocumentOut,
    status_code=201,
    summary="Upload a document: one or more PDF/JPEG files (multipart form)",
)
async def upload_document(
    patient_id: str,
    files: Annotated[list[UploadFile], File(description="PDF or JPEG files")],
    title: Annotated[str, Form(min_length=1, max_length=160)],
    category: Annotated[str, Form()] = "other",
    document_date: Annotated[str | None, Form(alias="documentDate", pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    note: Annotated[str | None, Form(max_length=1000)] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> DocumentOut:
    s = get_settings()
    pid = resolve_patient_id(user, patient_id)
    ctx = _access(db, user, pid)
    limiter.hit(f"doc-upload:{user.id}", 60, 3600)
    if category not in CATEGORIES:
        raise bad_request("invalid_category", f"Category must be one of: {', '.join(CATEGORIES)}")
    if not files or len(files) > s.max_document_files:
        raise bad_request("file_count", f"Attach between 1 and {s.max_document_files} files")

    loaded: list[tuple[str, str, str, bytes]] = []  # (filename, content type, extension, data)
    total = 0
    for f in files:
        data = await f.read(s.max_document_file_bytes + 1)
        if len(data) > s.max_document_file_bytes:
            raise bad_request("file_too_large", f"{f.filename}: each file can be at most {s.max_document_file_bytes // (1024 * 1024)} MB")
        kind = next((v for sig, v in SIGNATURES.items() if data.startswith(sig)), None)
        if kind is None:
            raise bad_request("unsupported_file", f"{f.filename}: only PDF and JPEG files can be uploaded")
        total += len(data)
        if total > s.max_document_upload_bytes:
            raise bad_request("upload_too_large", f"Files together can be at most {s.max_document_upload_bytes // (1024 * 1024)} MB")
        loaded.append((_safe_filename(f.filename, kind[1]), kind[0], kind[1], data))

    doc = Document(
        patient_id=pid,
        uploaded_by=user.id,
        uploader_role=user.role,
        title=title.strip(),
        category=category,
        document_date=document_date,
        note=(note or "").strip() or None,
    )
    db.add(doc)
    db.flush()
    storage = get_storage()
    for i, (name, ctype, ext, data) in enumerate(loaded):
        fid = uuid.uuid4()
        key = f"documents/{pid}/{doc.id}/{fid}.{ext}"
        storage.put(key, data, ctype)
        db.add(
            DocumentFile(
                id=fid,
                document_id=doc.id,
                filename=name,
                content_type=ctype,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                position=i,
            )
        )
    audit.record(
        db,
        actor=user,
        action="document.upload",
        patient_id=pid,
        resource_type="document",
        resource_id=doc.id,
        detail=f"{len(loaded)} file(s), {category}",
        meta=meta,
        grant_id=ctx.grant.id if ctx.grant else None,
    )
    if ctx.via == "grant":
        notify(
            db,
            user_id=pid,
            patient_id=pid,
            kind="sharing",
            title="New document in your records",
            body=f"{_display_name(db, user) or 'Your care team'} added “{doc.title}”.",
            link="/",
            dedupe_key=f"doc-upload:{doc.id}",
        )
    db.commit()
    return _out(db, user, ctx, doc)


@router.get("/patients/{patient_id}/documents", response_model=list[DocumentOut], summary="A patient's documents, newest first")
def list_documents(
    patient_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> list[DocumentOut]:
    pid = resolve_patient_id(user, patient_id)
    ctx = _access(db, user, pid)
    docs = list(
        db.scalars(
            select(Document)
            .where(Document.patient_id == pid, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
            .limit(200)
        )
    )
    if ctx.via == "grant":
        audit.record(db, actor=user, action="document.list", patient_id=pid, detail="Documents", meta=meta, grant_id=ctx.grant.id)
        db.commit()
    return [_out(db, user, ctx, d) for d in docs]


@router.delete("/documents/{document_id}", status_code=204, summary="Delete a document (the patient, or whoever uploaded it)")
def delete_document(
    document_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)
) -> None:
    doc, ctx = _doc(db, user, document_id)
    if not (ctx.via == "self" or doc.uploaded_by == user.id):
        raise forbidden("Only the patient or the person who uploaded it can delete this document")
    doc.deleted_at = utcnow()
    storage = get_storage()
    for f in db.scalars(select(DocumentFile).where(DocumentFile.document_id == doc.id)):
        if f.storage_key:
            storage.delete(f.storage_key)
            f.storage_key = None
    audit.record(
        db, actor=user, action="document.delete", patient_id=doc.patient_id, resource_type="document", resource_id=doc.id, meta=meta
    )
    db.commit()


@router.post("/documents/{document_id}/comments", response_model=CommentOut, status_code=201, summary="Doctors: comment on a document")
def add_comment(
    document_id: uuid.UUID,
    body: CommentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> CommentOut:
    doc, ctx = _doc(db, user, document_id)
    if not _can_comment(user, ctx):
        raise forbidden("Only the patient's linked doctors can comment")
    limiter.hit(f"doc-comment:{user.id}", 120, 3600)
    c = DocumentComment(document_id=doc.id, author_id=user.id, body=body.body.strip())
    db.add(c)
    db.flush()
    audit.record(
        db,
        actor=user,
        action="document.comment",
        patient_id=doc.patient_id,
        resource_type="document",
        resource_id=doc.id,
        meta=meta,
        grant_id=ctx.grant.id if ctx.grant else None,
    )
    notify(
        db,
        user_id=doc.patient_id,
        patient_id=doc.patient_id,
        kind="sharing",
        title="Your doctor commented on a document",
        body=f"{user.full_name or 'Your doctor'} commented on “{doc.title}”.",
        link="/",
        dedupe_key=f"doc-comment:{c.id}",
    )
    db.commit()
    return CommentOut(id=str(c.id), author_name=user.full_name, author_role=user.role, body=c.body, created_at=c.created_at, mine=True)


@router.delete("/documents/{document_id}/comments/{comment_id}", status_code=204, summary="Delete your own comment")
def delete_comment(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    meta: RequestMeta = Depends(request_meta),
) -> None:
    doc, _ = _doc(db, user, document_id)
    c = db.get(DocumentComment, comment_id)
    if c is None or c.document_id != doc.id or c.deleted_at is not None:
        raise not_found("Comment")
    if c.author_id != user.id:
        raise forbidden("You can only delete your own comments")
    c.deleted_at = utcnow()
    audit.record(
        db, actor=user, action="document.comment_delete", patient_id=doc.patient_id, resource_type="document", resource_id=doc.id, meta=meta
    )
    db.commit()


@router.post(
    "/documents/{document_id}/files/{file_id}/link",
    response_model=FileLinkOut,
    summary="Short-lived link to view or download one file (bound to you)",
)
def file_link(
    document_id: uuid.UUID, file_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> FileLinkOut:
    s = get_settings()
    doc, _ = _doc(db, user, document_id, download=True)
    f = db.get(DocumentFile, file_id)
    if f is None or f.document_id != doc.id or not f.storage_key:
        raise not_found("File")
    token = sign_payload({"t": "doc", "f": str(f.id), "u": str(user.id)}, s.document_link_ttl_s)
    return FileLinkOut(url=f"{s.api_base_url.rstrip('/')}/v1/documents/files/{token}", expires_in_s=s.document_link_ttl_s)


@router.get(
    "/documents/files/{token}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/jpeg": {}}}},
    summary="Open a file through its signed link (permission re-checked)",
)
def open_file(token: str, download: bool = False, db: Session = Depends(get_db), meta: RequestMeta = Depends(request_meta)) -> Response:
    gone = ApiError(410, "link_expired", "This link has expired. Open the document again.")
    body = verify_signed(token)
    if body is None or body.get("t") != "doc":
        raise gone
    try:
        f = db.get(DocumentFile, uuid.UUID(body["f"]))
        user = db.get(User, uuid.UUID(body["u"]))
    except (KeyError, ValueError) as exc:
        raise gone from exc
    if f is None or user is None or user.status != "active" or not f.storage_key:
        raise gone
    doc, ctx = _doc(db, user, f.document_id, download=True)
    data = get_storage().get(f.storage_key)
    if hashlib.sha256(data).hexdigest() != f.sha256:
        raise ApiError(500, "integrity_error", "Stored file failed its integrity check")
    if ctx.via == "grant":
        audit.record(
            db,
            actor=user,
            action="document.view",
            patient_id=doc.patient_id,
            resource_type="document",
            resource_id=doc.id,
            detail=f.filename,
            meta=meta,
            grant_id=ctx.grant.id if ctx.grant else None,
        )
        db.commit()
    disposition = "attachment" if download else "inline"
    ascii_name = f.filename.encode("ascii", "replace").decode().replace('"', "")
    headers = {
        "Content-Disposition": f'{disposition}; filename="{ascii_name}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if f.content_type == "image/jpeg":
        # Chrome's PDF viewer refuses sandboxed documents, so the strict policy goes on images only.
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return Response(content=data, media_type=f.content_type, headers=headers)
