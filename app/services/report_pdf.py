"""Server-side PDF health report.

Values are shown exactly as recorded. Days without data are listed (and drawn as gaps),
low-quality readings are counted, averages are labelled as averages, and the report says
it is not a substitute for clinical assessment.
"""

import io
import struct
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import KeepTogether, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.models import Device, EcgRecording, Measurement, Report, User
from app.schemas.common import iso_with_offset

TEAL = colors.HexColor("#0B7A69")
INK = colors.HexColor("#0A0F12")
MUTED = colors.HexColor("#566569")
LINE = colors.HexColor("#E1E8E6")
GAP = colors.HexColor("#EEF1F1")
CORAL = colors.HexColor("#C2410C")
NAMES = {
    "hr": "Heart rate",
    "spo2": "Blood oxygen (SpO2)",
    "bp": "Blood pressure",
    "resp": "Respiration rate",
    "temp": "Body temperature",
    "glucose": "Blood glucose",
}
UNITS = {"hr": "bpm", "spo2": "%", "bp": "mmHg", "resp": "breaths/min", "temp": "°C", "glucose": "mg/dL"}
DECIMALS = {"hr": 0, "spo2": 0, "bp": 0, "resp": 0, "temp": 1, "glucose": 0}

styles = getSampleStyleSheet()
H1 = ParagraphStyle(
    "h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=15, textColor=colors.HexColor("#0B3B34"), spaceAfter=4
)
H2 = ParagraphStyle(
    "h2",
    parent=styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=11.5,
    textColor=colors.HexColor("#0B3B34"),
    spaceBefore=10,
    spaceAfter=4,
)
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=MUTED)


@dataclass
class ReportParams:
    patient_id: uuid.UUID
    from_date: date
    to_date: date
    vitals: list[str]
    include_ecg: bool
    include_tables: bool
    include_name: bool
    utc_offset_min: int
    history_floor: datetime | None


class _NumberedCanvas(pdfcanvas.Canvas):
    """Adds 'Page X of Y' and the disclaimer footer on every page."""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab API
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(MUTED)
            self.drawString(
                15 * mm, 10 * mm, "Summary of recorded measurements — not a diagnosis and not a substitute for clinical assessment."
            )
            self.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _fmt(vital: str, v: float | None) -> str:
    return "—" if v is None else f"{v:.{DECIMALS[vital]}f}"


def _chart(
    days: list[date], values: dict[date, float], values2: dict[date, float] | None, width: float = 170 * mm, height: float = 38 * mm
) -> Drawing:
    d = Drawing(width, height)
    all_vals = list(values.values()) + (list(values2.values()) if values2 else [])
    if not all_vals:
        return d
    lo, hi = min(all_vals), max(all_vals)
    pad = (hi - lo) * 0.2 or 1
    lo, hi = lo - pad, hi + pad
    left, bottom, top = 24, 12, height - 4
    n = len(days)
    step = (width - left - 4) / max(1, n - 1)

    def x(i: int) -> float:
        return left + i * step

    def y(v: float) -> float:
        return bottom + (v - lo) / (hi - lo) * (top - bottom)

    for i, day in enumerate(days):
        if day not in values:
            d.add(Rect(x(i) - step / 2, bottom, step, top - bottom, fillColor=GAP, strokeColor=None))
    d.add(Line(left, bottom, width - 4, bottom, strokeColor=LINE))
    fmt = ".1f" if hi - lo < 10 else ".0f"  # narrow ranges need a decimal so the two labels differ
    d.add(String(0, top - 6, f"{hi:{fmt}}", fontSize=6.5, fillColor=MUTED))
    d.add(String(0, bottom, f"{lo:{fmt}}", fontSize=6.5, fillColor=MUTED))
    for i in (0, n // 2, n - 1):
        d.add(String(x(i) - 10, 1, days[i].strftime("%d %b"), fontSize=6.5, fillColor=MUTED))

    def draw(series: dict[date, float], color: colors.Color, dash: list[int] | None) -> None:
        seg: list[float] = []
        for i, day in enumerate(days):
            if day in series:
                seg += [x(i), y(series[day])]
            elif seg:
                if len(seg) >= 4:
                    d.add(PolyLine(seg, strokeColor=color, strokeWidth=1.2, strokeDashArray=dash))
                seg = []
        if len(seg) >= 4:
            d.add(PolyLine(seg, strokeColor=color, strokeWidth=1.2, strokeDashArray=dash))
        # Single isolated days are drawn as dots so they aren't lost.
        for i, day in enumerate(days):
            if day in series and (i == 0 or days[i - 1] not in series) and (i == n - 1 or days[i + 1] not in series):
                d.add(Rect(x(i) - 1.2, y(series[day]) - 1.2, 2.4, 2.4, fillColor=color, strokeColor=None))

    draw(values, TEAL, None)
    if values2:
        draw(values2, CORAL, [3, 2])
    return d


def _ecg_strip(samples: bytes, rec: EcgRecording, seconds: float = 10) -> Drawing:
    px_mm = 0.62 * mm  # drawing scale: 1 mm of ECG paper
    px_s = rec.mm_per_sec * px_mm
    px_mv = rec.mm_per_mv * px_mm
    w, h = seconds * px_s, 30 * px_mm
    d = Drawing(w, h)
    minor, major = colors.HexColor("#F6C9BE"), colors.HexColor("#EE9C8A")
    k = 0
    while k * px_mm <= w:
        d.add(Line(k * px_mm, 0, k * px_mm, h, strokeColor=major if k % 5 == 0 else minor, strokeWidth=0.5 if k % 5 == 0 else 0.2))
        k += 1
    k = 0
    while k * px_mm <= h:
        d.add(Line(0, k * px_mm, w, k * px_mm, strokeColor=major if k % 5 == 0 else minor, strokeWidth=0.5 if k % 5 == 0 else 0.2))
        k += 1
    for seg in rec.poor_quality_segments or []:
        if seg["from_sec"] < seconds:
            d.add(
                Rect(
                    seg["from_sec"] * px_s,
                    0,
                    (min(seconds, seg["to_sec"]) - seg["from_sec"]) * px_s,
                    h,
                    fillColor=colors.Color(0.76, 0.25, 0.05, alpha=0.12),
                    strokeColor=None,
                )
            )
    n = min(rec.sample_count, int(seconds * rec.sample_rate_hz))
    vals = struct.unpack(f"<{n}h", samples[: n * 2])
    stride = max(1, rec.sample_rate_hz // 125)
    base = h * 0.6
    pts: list[float] = []
    for i in range(0, n, stride):
        pts += [i / rec.sample_rate_hz * px_s, base + vals[i] / 1000 * px_mv]
    if len(pts) >= 4:
        d.add(PolyLine(pts, strokeColor=INK, strokeWidth=0.6))
    return d


def build_pdf(db: Session, p: ReportParams, generated_at: datetime) -> tuple[bytes, int]:
    tz = timezone(timedelta(minutes=p.utc_offset_min))
    start = datetime.combine(p.from_date, time.min, tz)
    end = datetime.combine(p.to_date + timedelta(days=1), time.min, tz)
    if p.history_floor is not None and start < p.history_floor:
        start = p.history_floor
    days = [p.from_date + timedelta(days=i) for i in range((p.to_date - p.from_date).days + 1)]
    patient = db.get(User, p.patient_id)
    if patient is None:
        raise ValueError("Patient not found")

    rows = list(
        db.scalars(
            select(Measurement)
            .where(
                Measurement.patient_id == p.patient_id,
                Measurement.vital.in_(p.vitals),
                Measurement.measured_at >= start.astimezone(UTC),
                Measurement.measured_at < end.astimezone(UTC),
            )
            .order_by(Measurement.measured_at)
        )
    )
    by_vital: dict[str, list[Measurement]] = defaultdict(list)
    for m in rows:
        by_vital[m.vital].append(m)
    simulated = any(m.acquisition == "simulated" for m in rows)

    story: list = []
    story.append(Paragraph('Cardiosens<font color="#0B7A69">AI</font> — Health summary', H1))
    if simulated:
        story.append(Paragraph('<font color="#2F3BA8"><b>CONTAINS SIMULATED DEMO DATA — NOT REAL MEASUREMENTS</b></font>', BODY))
    who = (
        f"{patient.full_name} · ID {patient.patient_code}" if p.include_name and patient.full_name else f"Patient ID {patient.patient_code}"
    )
    meta = [
        ["Patient", who, "Generated", generated_at.astimezone(tz).strftime("%d %b %Y %H:%M")],
        [
            "Period",
            f"{p.from_date:%d %b %Y} – {p.to_date:%d %b %Y}",
            "Time zone",
            f"UTC{'+' if p.utc_offset_min >= 0 else '-'}{abs(p.utc_offset_min) // 60:02d}:{abs(p.utc_offset_min) % 60:02d}",
        ],
    ]
    if p.history_floor is not None and p.history_floor > datetime.combine(p.from_date, time.min, tz):
        meta.append(["Note", f"Sharing limits this report to data from {p.history_floor.astimezone(tz):%d %b %Y}", "", ""])
    t = Table(meta, colWidths=[22 * mm, 78 * mm, 22 * mm, 48 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story += [t, Spacer(1, 4)]

    notes: list[str] = []
    summary = [["Vital sign", "Average", "Range", "Readings", "Days without data"]]
    sections: list = []
    for vital in p.vitals:
        ms = by_vital.get(vital, [])
        if not ms:
            summary.append([NAMES[vital], "No readings in this period", "", "", ""])
            if vital == "glucose":
                notes.append("Blood glucose: no readings — values are never estimated.")
            continue
        daily: dict[date, list[float]] = defaultdict(list)
        daily2: dict[date, list[float]] = defaultdict(list)
        for m in ms:
            local_day = m.measured_at.astimezone(tz).date()
            if vital == "bp":
                daily[local_day].append(float(m.systolic or 0))
                daily2[local_day].append(float(m.diastolic or 0))
            else:
                daily[local_day].append(float(m.value or 0))
        empty = sum(1 for dd in days if dd not in daily)
        poor = sum(1 for m in ms if m.quality == "poor")
        if vital == "bp":
            sys = [m.systolic or 0 for m in ms]
            dia = [m.diastolic or 0 for m in ms]
            avg = f"{sum(sys) / len(sys):.0f}/{sum(dia) / len(dia):.0f} mmHg"
            rng = f"{min(sys)}–{max(sys)} / {min(dia)}–{max(dia)}"
        else:
            vals = [m.value or 0.0 for m in ms]
            avg = f"{_fmt(vital, sum(vals) / len(vals))} {UNITS[vital]}"
            rng = f"{_fmt(vital, min(vals))}–{_fmt(vital, max(vals))}"
        summary.append([NAMES[vital], avg, rng, f"{len(ms):,}", f"{empty}" if empty else "none"])
        if empty:
            notes.append(f"{NAMES[vital]}: {empty} day(s) without readings are shown as gaps, not filled in.")
        if poor:
            notes.append(f"{NAMES[vital]}: {poor} low-quality reading(s) are included and counted, not removed.")
        if any(m.acquisition == "manual" for m in ms):
            notes.append(f"{NAMES[vital]}: includes manually entered readings (labelled 'Manual').")

        avg_by_day = {dd: sum(v) / len(v) for dd, v in daily.items()}
        avg2_by_day = {dd: sum(v) / len(v) for dd, v in daily2.items()} if vital == "bp" else None
        block = [Paragraph(f"{NAMES[vital]} — daily average ({UNITS[vital]})", H2), _chart(days, avg_by_day, avg2_by_day)]
        if vital == "bp":
            block.append(Paragraph("Solid: systolic · dashed: diastolic · grey: no data", SMALL))
        sections.append(KeepTogether(block))
        if p.include_tables:
            data = [["Measured (local time)", f"Value ({UNITS[vital]})", "How", "Quality"]]
            for m in ms:
                v = f"{m.systolic}/{m.diastolic}" if vital == "bp" else _fmt(vital, m.value)
                how = {"live": "Live", "delayed": "Synced later", "manual": "Manual", "simulated": "Simulated"}[m.acquisition]
                data.append(
                    [
                        iso_with_offset(m.measured_at, m.utc_offset_min).replace("T", " ")[:16],
                        v,
                        how,
                        {"good": "Good", "poor": "Low", "unknown": "n/a"}[m.quality],
                    ]
                )
            lt = LongTable(data, colWidths=[45 * mm, 35 * mm, 35 * mm, 25 * mm], repeatRows=1)
            lt.setStyle(
                TableStyle(
                    [
                        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
                        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                    ]
                )
            )
            sections += [Spacer(1, 3), lt]

    st = Table(summary, colWidths=[40 * mm, 45 * mm, 38 * mm, 20 * mm, 27 * mm])
    st.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
            ]
        )
    )
    story += [Paragraph("Summary", H2), st, *sections]

    if p.include_ecg:
        recs = list(
            db.scalars(
                select(EcgRecording)
                .where(
                    EcgRecording.patient_id == p.patient_id,
                    EcgRecording.started_at >= start.astimezone(UTC),
                    EcgRecording.started_at < end.astimezone(UTC),
                )
                .order_by(EcgRecording.started_at)
            )
        )
        if recs:
            story += [PageBreak(), Paragraph(f"ECG recordings ({len(recs)})", H2)]
            storage = get_storage()
            for rec in recs:
                desc = (
                    f"<b>{iso_with_offset(rec.started_at, rec.utc_offset_min).replace('T', ' ')[:16]}</b> · {rec.lead} · "
                    f"{rec.sample_count / rec.sample_rate_hz:.0f} s at {rec.sample_rate_hz} Hz · "
                    f"{rec.mm_per_sec:g} mm/s, {rec.mm_per_mv:g} mm/mV"
                    + (f" · {rec.average_hr} bpm avg" if rec.average_hr else "")
                    + (' · <font color="#9A3412">low signal in shaded sections</font>' if rec.poor_quality_segments else "")
                    + f". First 10 s shown; recording ID {rec.id}."
                )
                story.append(KeepTogether([Paragraph(desc, BODY), _ecg_strip(storage.get(rec.storage_key), rec), Spacer(1, 6)]))
            story.append(Paragraph("No automated interpretation is included.", SMALL))

    device_ids = {m.device_id for m in rows if m.device_id}
    devices = [db.get(Device, i) for i in device_ids]
    sources = "; ".join(f"{d.display_name} ({d.model.name}, serial {d.serial})" for d in devices if d) or "none"
    story += [Paragraph("Data notes", H2)]
    for n in notes or ["No gaps or quality issues in the selected period."]:
        story.append(Paragraph(f"• {n}", BODY))
    story += [
        Spacer(1, 6),
        Paragraph(f"Sources: {sources}.", SMALL),
        Paragraph(
            "Values are shown as recorded; missing periods are not filled in and averages are labelled as averages. "
            "This report summarises recorded measurements. It is not a diagnosis and not a substitute for clinical assessment.",
            SMALL,
        ),
    ]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title="CardiosensAI health summary",
        author="CardiosensAI",
    )
    doc.build(story, canvasmaker=_NumberedCanvas)
    pdf = buf.getvalue()
    return pdf, pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def generate_report(report_id: uuid.UUID) -> None:
    """Runs after the request returns (FastAPI BackgroundTasks). Move to a job queue at scale."""
    from app.core.db import SessionLocal, utcnow

    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if r is None or r.status != "pending":
            return
        prm = r.params
        params = ReportParams(
            patient_id=r.patient_id,
            from_date=date.fromisoformat(prm["fromDate"]),
            to_date=date.fromisoformat(prm["toDate"]),
            vitals=prm["vitals"],
            include_ecg=prm["includeEcg"],
            include_tables=prm["includeTables"],
            include_name=prm["includeName"],
            utc_offset_min=prm["utcOffsetMin"],
            history_floor=datetime.fromisoformat(prm["historyFloor"]) if prm.get("historyFloor") else None,
        )
        try:
            pdf, pages = build_pdf(db, params, utcnow())
            key = f"reports/{r.patient_id}/{r.id}.pdf"
            get_storage().put(key, pdf, "application/pdf")
            r.storage_key, r.size_bytes, r.pages, r.status = key, len(pdf), pages, "ready"
        except Exception:  # noqa: BLE001 - record failure, never leak details to the client
            import logging

            logging.getLogger("cardiosensai.reports").exception("report_failed")
            r.status, r.error = "failed", "Report generation failed. Try again or contact support."
        r.completed_at = utcnow()
        db.commit()
    finally:
        db.close()
