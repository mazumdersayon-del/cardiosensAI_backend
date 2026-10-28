import { useEffect, useRef, useState } from 'react';
import { api, apiUpload, type DocumentFile, type MedDocument } from '../api';
import { day, ROLE_LABEL, when } from '../format';
import { Banner, errorText, useLoad } from './ui';

export const CATEGORIES: Record<string, string> = {
  lab: 'Lab report',
  prescription: 'Prescription',
  imaging: 'Imaging / scan',
  discharge: 'Discharge summary',
  ecg: 'ECG / cardiology',
  other: 'Other',
};
const MAX_FILES = 10;
const MAX_FILE_MB = 15;
const ACCEPT = '.pdf,.jpg,.jpeg,application/pdf,image/jpeg';

function sizeText(bytes: number): string {
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Client-side check (the server checks the file contents again). */
export function fileProblem(f: File): string | null {
  const okType = f.type === 'application/pdf' || f.type === 'image/jpeg' || /\.(pdf|jpe?g)$/i.test(f.name);
  if (!okType) return `${f.name}: only PDF and JPEG files`;
  if (f.size > MAX_FILE_MB * 1024 * 1024) return `${f.name}: larger than ${MAX_FILE_MB} MB`;
  return null;
}

async function fileLink(docId: string, file: DocumentFile): Promise<string> {
  return (await api<{ url: string }>('POST', `/v1/documents/${docId}/files/${file.id}/link`)).url;
}

function UploadForm({ patientId, onDone }: { patientId: string; onDone: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('lab');
  const [date, setDate] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    if (!list) return;
    const next = [...files, ...Array.from(list)].slice(0, MAX_FILES);
    setFiles(next);
    const problem = next.map(fileProblem).find(Boolean);
    setMsg(problem ? { kind: 'error', text: problem } : null);
    if (!title && list[0]) setTitle(list[0].name.replace(/\.[^.]+$/, ''));
    if (input.current) input.current.value = '';
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const problem = files.map(fileProblem).find(Boolean);
    if (problem || !files.length) {
      setMsg({ kind: 'error', text: problem ?? 'Choose at least one file' });
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append('files', f, f.name));
    form.append('title', title.trim());
    form.append('category', category);
    if (date) form.append('documentDate', date);
    if (note.trim()) form.append('note', note.trim());
    setBusy(true);
    setMsg(null);
    try {
      await apiUpload(`/v1/patients/${patientId}/documents`, form);
      setFiles([]);
      setTitle('');
      setNote('');
      setDate('');
      setMsg({ kind: 'ok', text: 'Uploaded.' });
      onDone();
    } catch (ex) {
      setMsg({ kind: 'error', text: errorText(ex) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack upload-box" onSubmit={submit}>
      <div
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          add(e.dataTransfer.files);
        }}
      >
        <input ref={input} type="file" accept={ACCEPT} multiple onChange={(e) => add(e.target.files)} id={`up-${patientId}`} className="sr-only" />
        <label htmlFor={`up-${patientId}`} className="btn ghost small">
          Choose files
        </label>
        <span className="muted">or drop them here · PDF or JPEG · up to {MAX_FILES} files, {MAX_FILE_MB} MB each</span>
      </div>
      {files.length > 0 && (
        <ul className="file-chips">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className={fileProblem(f) ? 'bad' : ''}>
              <span>{/pdf$/i.test(f.name) || f.type === 'application/pdf' ? 'PDF' : 'JPEG'}</span> {f.name} <small>{sizeText(f.size)}</small>
              <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: '2 1 220px' }}>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={160} placeholder="e.g. Lipid profile" />
        </label>
        <label className="field" style={{ flex: '1 1 160px' }}>
          Type
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {Object.entries(CATEGORIES).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: '1 1 150px' }}>
          Date on document
          <input type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} />
        </label>
      </div>
      <label className="field">
        Note (optional)
        <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="Anything the reader should know" />
      </label>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      <div>
        <button type="submit" className="btn primary" disabled={busy || !files.length}>
          {busy ? 'Uploading…' : `Upload ${files.length > 1 ? `${files.length} files` : 'document'}`}
        </button>
      </div>
    </form>
  );
}

function Thumb({ docId, file }: { docId: string; file: DocumentFile }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    if (file.contentType === 'image/jpeg') fileLink(docId, file).then((u) => live && setSrc(u)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [docId, file]);

  async function open(download = false) {
    const w = download ? null : window.open('', '_blank'); // open now so the browser doesn't block it as a pop-up
    try {
      const url = await fileLink(docId, file);
      if (download) window.location.assign(`${url}?download=true`);
      else if (w) {
        w.opener = null;
        w.location.href = url;
      }
    } catch {
      w?.close();
    }
  }

  return (
    <div className="doc-file">
      <button type="button" className="doc-thumb" onClick={() => open()} title={`Open ${file.filename}`}>
        {file.contentType === 'image/jpeg' && src ? <img src={src} alt={file.filename} loading="lazy" /> : <span className="pdf-badge">{file.contentType === 'application/pdf' ? 'PDF' : 'JPEG'}</span>}
      </button>
      <div className="doc-file-meta">
        <span title={file.filename}>{file.filename}</span>
        <small className="muted">
          {sizeText(file.sizeBytes)} ·{' '}
          <button type="button" className="linklike" onClick={() => open(true)}>
            Download
          </button>
        </small>
      </div>
    </div>
  );
}

function Comments({ doc, onChange }: { doc: MedDocument; onChange: () => void }) {
  const [body, setBody] = useState('');
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="comments">
      {doc.comments.map((c) => (
        <div key={c.id} className="comment">
          <div className="row spread" style={{ gap: 6 }}>
            <strong>
              {c.authorName ?? 'Removed account'} {c.authorRole && <span className="pill ok">{ROLE_LABEL[c.authorRole]}</span>}
            </strong>
            <small className="muted">
              {when(c.createdAt)}
              {c.mine && (
                <>
                  {' · '}
                  <button
                    type="button"
                    className="linklike"
                    onClick={async () => {
                      await api('DELETE', `/v1/documents/${doc.id}/comments/${c.id}`).catch((e) => setErr(errorText(e)));
                      onChange();
                    }}
                  >
                    Delete
                  </button>
                </>
              )}
            </small>
          </div>
          <p style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{c.body}</p>
        </div>
      ))}
      {doc.canComment && (
        <form
          className="row"
          style={{ alignItems: 'flex-end' }}
          onSubmit={async (e) => {
            e.preventDefault();
            setErr(null);
            try {
              await api('POST', `/v1/documents/${doc.id}/comments`, { body });
              setBody('');
              onChange();
            } catch (ex) {
              setErr(errorText(ex));
            }
          }}
        >
          <label className="field" style={{ flex: 1 }}>
            <span className="sr-only">Add a comment</span>
            <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} maxLength={4000} placeholder="Add a comment for the patient and care team" required />
          </label>
          <button type="submit" className="btn primary small" disabled={!body.trim()}>
            Comment
          </button>
        </form>
      )}
      {err && <Banner kind="error">{err}</Banner>}
    </div>
  );
}

/** Uploaded documents for one patient (patient: 'me'). Patients and linked doctors/clinics upload; doctors comment. */
/** One document: title, type, who uploaded it, files, note, comments, delete. */
export function DocumentItem({ d, onChange }: { d: MedDocument; onChange: () => void }) {
  const [confirm, setConfirm] = useState(false);
  return (
    <article className="doc-card">
      <header className="row spread">
        <div>
          <h3 style={{ margin: 0 }}>{d.title}</h3>
          <small className="muted">
            {CATEGORIES[d.category] ?? d.category}
            {d.documentDate ? ` · dated ${day(d.documentDate)}` : ''} · uploaded by {d.uploaderName ?? 'removed account'} (
            {ROLE_LABEL[d.uploaderRole] ?? d.uploaderRole}) {when(d.createdAt)}
          </small>
        </div>
        {d.canDelete &&
          (confirm ? (
            <div className="row" style={{ gap: 6 }}>
              <button
                type="button"
                className="btn danger small"
                onClick={async () => {
                  await api('DELETE', `/v1/documents/${d.id}`);
                  setConfirm(false);
                  onChange();
                }}
              >
                Delete
              </button>
              <button type="button" className="btn ghost small" onClick={() => setConfirm(false)}>
                Keep
              </button>
            </div>
          ) : (
            <button type="button" className="btn ghost small" onClick={() => setConfirm(true)}>
              Delete…
            </button>
          ))}
      </header>
      {d.note && <p style={{ margin: 0 }}>{d.note}</p>}
      <div className="doc-files">
        {d.files.map((f) => (
          <Thumb key={f.id} docId={d.id} file={f} />
        ))}
      </div>
      {(d.comments.length > 0 || d.canComment) && <Comments doc={d} onChange={onChange} />}
    </article>
  );
}

/** The upload form behind a button (patient home: "Reports and documents" card; the History card lists what was uploaded). */
export function DocumentUpload({ patientId, onDone }: { patientId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 10 }}>
        <button type="button" className={`btn ${open ? 'ghost' : 'primary'} small`} onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Close' : 'Upload documents'}
        </button>
        {done && !open && <small className="muted">Uploaded. It is at the top of History.</small>}
      </div>
      {open && (
        <UploadForm
          patientId={patientId}
          onDone={() => {
            setOpen(false);
            setDone(true);
            onDone();
          }}
        />
      )}
    </div>
  );
}

export default function DocumentsPanel({ patientId }: { patientId: string }) {
  const [docs, err, reload] = useLoad(() => api<MedDocument[]>('GET', `/v1/patients/${patientId}/documents`), [patientId]);
  const [showUpload, setShowUpload] = useState(false);
  const [filter, setFilter] = useState('all');
  const shown = (docs ?? []).filter((d) => filter === 'all' || d.category === filter);
  return (
    <div className="stack">
      <div className="row spread">
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by type" style={{ maxWidth: 220 }}>
          <option value="all">All types</option>
          {Object.entries(CATEGORIES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <button type="button" className={`btn ${showUpload ? 'ghost' : 'primary'} small`} onClick={() => setShowUpload(!showUpload)}>
          {showUpload ? 'Close' : 'Upload documents'}
        </button>
      </div>
      {showUpload && (
        <UploadForm
          patientId={patientId}
          onDone={() => {
            reload();
            setShowUpload(false);
          }}
        />
      )}
      {err && <p className="muted">{err}</p>}
      {docs && !shown.length && <div className="empty">{docs.length ? 'No documents of this type.' : 'No documents yet. Upload lab reports, prescriptions or scans as PDF or JPEG.'}</div>}
      {shown.map((d) => (
        <DocumentItem key={d.id} d={d} onChange={reload} />
      ))}
    </div>
  );
}
