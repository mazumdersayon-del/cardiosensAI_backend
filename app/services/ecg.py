"""ECG rules shared by uploads and live sessions: lead names, live state, turning a live session into a recording."""

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.ecg_leads import STANDARD_12
from app.core.errors import bad_request
from app.core.storage import get_storage
from app.models import EcgLiveChunk, EcgLiveSession, EcgRecording

LIVE_STALLED_AFTER = timedelta(seconds=5)
LIVE_ABANDONED_AFTER = timedelta(minutes=2)
MAX_CHUNK_SECONDS = 10


def check_leads(leads: list[str]) -> list[str]:
    if not leads or len(set(leads)) != len(leads) or any(lead not in STANDARD_12 for lead in leads):
        raise bad_request("invalid_leads", f"Leads must be distinct names from: {', '.join(STANDARD_12)}")
    return leads


def lead_label(leads: list[str]) -> str:
    if len(leads) == 12:
        return "12-lead"
    return f"Lead {leads[0]}" if len(leads) == 1 else f"{len(leads)}-lead"


def live_state(s: EcgLiveSession) -> str:
    if s.status != "live":
        return "stopped"
    last = s.last_chunk_at or s.started_at
    return "live" if utcnow() - last < LIVE_STALLED_AFTER else "stalled"


def finalize(db: Session, s: EcgLiveSession, quality: str = "unknown") -> EcgRecording | None:
    """Join the chunks into one stored recording (frames in seq order) and end the session.

    A session with no data ends without a recording. Caller commits.
    """
    if s.status != "live":
        return db.get(EcgRecording, s.recording_id) if s.recording_id else None
    s.status = "stopped"
    s.ended_at = utcnow()
    chunks = list(db.scalars(select(EcgLiveChunk).where(EcgLiveChunk.session_id == s.id).order_by(EcgLiveChunk.seq)))
    raw = b"".join(c.data for c in chunks)
    db.execute(delete(EcgLiveChunk).where(EcgLiveChunk.session_id == s.id))
    if not raw:
        return None
    rec_id = uuid.uuid4()
    key = f"ecg/{s.patient_id}/{rec_id}.i16"
    get_storage().put(key, raw, "application/octet-stream")
    rec = EcgRecording(
        id=rec_id,
        patient_id=s.patient_id,
        client_id=f"live:{s.id}",
        device_id=s.device_id,
        started_at=s.started_at,
        utc_offset_min=s.utc_offset_min,
        lead=lead_label(s.leads),
        leads=list(s.leads),
        sample_rate_hz=s.sample_rate_hz,
        sample_count=len(raw) // (2 * len(s.leads)),
        mm_per_mv=s.mm_per_mv,
        mm_per_sec=s.mm_per_sec,
        average_hr=s.average_hr,
        poor_quality_segments=[],
        quality=quality,
        acquisition=s.acquisition or "live",
        storage_key=key,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    db.add(rec)
    db.flush()
    s.recording_id = rec.id
    return rec


def close_abandoned(db: Session, patient_id: uuid.UUID) -> None:
    """Sessions whose device went silent for 2 minutes become recordings of what arrived. Caller commits."""
    cutoff = utcnow() - LIVE_ABANDONED_AFTER
    for s in db.scalars(select(EcgLiveSession).where(EcgLiveSession.patient_id == patient_id, EcgLiveSession.status == "live")):
        if (s.last_chunk_at or s.started_at) < cutoff:
            finalize(db, s, quality="unknown")
