import { useState } from 'react';
import { api, type MedDocument, type Report } from '../api';
import { day, when } from '../format';
import { CATEGORIES, DocumentItem } from './Documents';
import { downloadReport } from './Reports';
import { errorText, StatusPill, useLoad } from './ui';

type Entry = { kind: 'doc'; at: string; doc: MedDocument } | { kind: 'report'; at: string; report: Report };

/** Patient home: every uploaded document and generated PDF report, newest first, in one vertical list. */
export default function FileHistory({ patientId, reloadKey }: { patientId: string; reloadKey: number }) {
  const [docs, docErr, reloadDocs] = useLoad(() => api<MedDocument[]>('GET', `/v1/patients/${patientId}/documents`), [patientId, reloadKey]);
  const [reports, repErr] = useLoad(() => api<Report[]>('GET', `/v1/reports?patientId=${patientId}`), [patientId, reloadKey]);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const entries: Entry[] = [
    ...(docs ?? []).map((d): Entry => ({ kind: 'doc', at: d.createdAt, doc: d })),
    ...(reports ?? []).map((r): Entry => ({ kind: 'report', at: r.createdAt, report: r })),
  ].sort((a, b) => b.at.localeCompare(a.at));

  if (docErr || repErr) return <p className="muted">{docErr ?? repErr}</p>;
  if (!docs || !reports) return <p className="muted">Loading…</p>;
  if (!entries.length) return <div className="empty">Nothing yet. Uploaded documents and PDF reports appear here.</div>;
  return (
    <ol className="file-history">
      {err && <li className="muted">{err}</li>}
      {entries.map((e) =>
        e.kind === 'doc' ? (
          <li key={`d${e.doc.id}`}>
            <button type="button" className="fh-row" aria-expanded={open === e.doc.id} onClick={() => setOpen(open === e.doc.id ? null : e.doc.id)}>
              <span className="fh-kind">{CATEGORIES[e.doc.category] ?? e.doc.category}</span>
              <strong>{e.doc.title}</strong>
              <small className="muted">
                {e.doc.documentDate ? `${day(e.doc.documentDate)} · ` : ''}
                {e.doc.files.length} file{e.doc.files.length === 1 ? '' : 's'}
                {e.doc.comments.length ? ` · ${e.doc.comments.length} comment${e.doc.comments.length === 1 ? '' : 's'}` : ''} · {when(e.at)}
              </small>
            </button>
            {open === e.doc.id && <DocumentItem d={e.doc} onChange={reloadDocs} />}
          </li>
        ) : (
          <li key={`r${e.report.id}`}>
            <div className="fh-row">
              <span className="fh-kind">PDF report</span>
              <strong>
                {day(e.report.params.fromDate)} – {day(e.report.params.toDate)}
              </strong>
              <div className="row" style={{ gap: 8 }}>
                <StatusPill status={e.report.status} />
                <small className="muted">{when(e.at)}</small>
                {e.report.status === 'ready' && (
                  <button type="button" className="btn ghost small" onClick={() => downloadReport(e.report).catch((x) => setErr(errorText(x)))}>
                    Download
                  </button>
                )}
              </div>
            </div>
          </li>
        ),
      )}
    </ol>
  );
}
