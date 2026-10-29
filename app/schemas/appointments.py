from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from app.schemas.common import CamelModel

Mode = Literal["online", "in_person"]


class WeeklyHours(CamelModel):
    day: int = Field(ge=0, le=6, description="0 = Monday … 6 = Sunday")
    start: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["10:00"])
    end: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["13:00"])


class CalendarSettingsIn(CamelModel):
    enabled: bool | None = Field(default=None, description="Patients can book")
    timezone: str | None = Field(default=None, max_length=64)
    slot_min: int | None = None
    window_days: int | None = None
    weekly_hours: list[WeeklyHours] | None = Field(default=None, max_length=50)
    online: bool | None = None
    in_person: bool | None = None
    meeting_link: str | None = Field(default=None, max_length=300)


class OfflinePeriodOut(CamelModel):
    id: str
    starts_at: datetime
    ends_at: datetime
    kind: str
    reason: str | None


class OfflineStatus(CamelModel):
    offline: bool
    until: datetime | None
    periods: list[OfflinePeriodOut] = Field(description="Current and future offline periods")


class CalendarSettingsOut(CamelModel):
    enabled: bool
    timezone: str
    slot_min: int
    window_days: int
    weekly_hours: list[WeeklyHours]
    online: bool
    in_person: bool
    meeting_link: str | None
    bookable: bool = Field(description="Patients can book now (on, hours set, and the account can see patients)")
    hint: str | None = Field(default=None, description="Why patients can't book yet")
    offline: OfflineStatus


class GoOfflineIn(CamelModel):
    until: AwareDatetime | None = Field(default=None, description="Default: 2 hours from now")


class OfflinePeriodIn(CamelModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    reason: str | None = Field(default=None, max_length=120)


class SlotOut(CamelModel):
    starts_at: datetime
    ends_at: datetime
    online: bool = Field(description="Can be booked as an online appointment")
    in_person: bool = Field(description="Can be booked as an in-person visit")
    taken: bool = False
    offline: bool = False


class PersonOut(CamelModel):
    unique_id: str | None
    full_name: str


class AppointmentOut(CamelModel):
    id: str
    starts_at: datetime
    ends_at: datetime
    mode: Mode
    status: str
    source: str
    reason: str | None
    doctor_note: str | None
    meeting_link: str | None = Field(description="Online and accepted only")
    doctor: PersonOut
    patient: PersonOut
    patient_linked: bool = Field(description="Doctor's view: the patient has an active link (their record can be opened)")
    patient_record_id: str | None = Field(default=None, description="Doctor's view, linked patients only: id for /patients/{id}")
    link_pending: bool = Field(default=False, description="A link request between them is waiting for the doctor")
    practice: str | None = Field(default=None, description="Where in-person visits take place")
    created_at: datetime


class CalendarWeekOut(CamelModel):
    timezone: str
    slots: list[SlotOut]
    appointments: list[AppointmentOut]
    offline_periods: list[OfflinePeriodOut]


class BookIn(CamelModel):
    doctor_id: str = Field(description="The doctor's 16-digit ID")
    starts_at: AwareDatetime
    mode: Mode
    reason: str | None = Field(default=None, max_length=300)
    allow_link: bool = Field(
        default=False, description="Also ask to link with this doctor (they see my records once they accept the appointment)"
    )


class WalkInIn(CamelModel):
    starts_at: AwareDatetime
    mode: Mode = "in_person"
    patient_id: str | None = Field(default=None, description="A linked patient's 16-digit ID")
    name: str | None = Field(default=None, max_length=120, description="Or the visitor's name")


class DecisionIn(CamelModel):
    action: Literal["accept", "decline", "cancel", "complete", "no_show"]
    note: str | None = Field(default=None, max_length=300)
    meeting_link: str | None = Field(
        default=None, max_length=300, description="Accepting an online appointment: overrides the default link"
    )


class DoctorCalendarOut(CamelModel):
    doctor: PersonOut
    bookable: bool
    reason: str | None
    timezone: str
    online: bool
    in_person: bool
    offline_until: datetime | None
    practice: str | None
    city: str | None
    window_days: int
    limit_reached: bool = Field(description="The patient already has the most open appointments allowed with this doctor")
    link_status: Literal["pending", "active"] | None = Field(default=None, description="The patient's link with this doctor, if any")
    slots: list[SlotOut]


class RangeStats(CamelModel):
    served: int
    served_online: int
    served_in_person: int
    patients: int
    no_show: int
    cancelled: int
    declined: int
    expired: int
    to_mark: int


class AppointmentStats(CamelModel):
    pending: int
    upcoming: int
    upcoming_online: int
    upcoming_in_person: int
    upcoming_today: int
    next_at: datetime | None
    ranges: dict[str, RangeStats] = Field(description="today, 7d, 30d, all")
