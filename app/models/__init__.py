"""SQLAlchemy models. Import everything here so Alembic sees the full metadata."""

from app.models.appointment import Appointment, DoctorOfflinePeriod, DoctorSchedule
from app.models.audit import AuditEvent
from app.models.auth import ActionToken, OtpChallenge, RefreshToken
from app.models.device import Device, DeviceModel
from app.models.document import Document, DocumentComment, DocumentFile
from app.models.fleet import DeviceConfig, DeviceNonce, DeviceOutbox, DevicePairing, FactoryDevice
from app.models.health import EcgLiveChunk, EcgLiveSession, EcgRecording, IngestionRejection, Measurement
from app.models.link import CareLink
from app.models.message import Message
from app.models.notification import Notification
from app.models.patient_profile import PatientProfile
from app.models.report import Report
from app.models.sharing import AccessGrant, Invite
from app.models.snapshot import EcgSnapshot, EcgSnapshotLabel
from app.models.user import Consent, Organization, ProfessionalProfile, User, VerificationFile

__all__ = [
    "Message",
    "PatientProfile",
    "AccessGrant",
    "ActionToken",
    "Appointment",
    "CareLink",
    "AuditEvent",
    "Consent",
    "Device",
    "DeviceConfig",
    "DeviceModel",
    "DeviceNonce",
    "DeviceOutbox",
    "DevicePairing",
    "Document",
    "DocumentComment",
    "DocumentFile",
    "DoctorOfflinePeriod",
    "DoctorSchedule",
    "EcgLiveChunk",
    "EcgLiveSession",
    "EcgRecording",
    "EcgSnapshot",
    "EcgSnapshotLabel",
    "FactoryDevice",
    "IngestionRejection",
    "Invite",
    "Measurement",
    "Notification",
    "Organization",
    "OtpChallenge",
    "ProfessionalProfile",
    "RefreshToken",
    "Report",
    "User",
    "VerificationFile",
]
