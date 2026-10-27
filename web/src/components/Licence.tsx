import { useRef, useState } from 'react';
import { api, apiUpload, openSignedLink, type LicenceFile, type MyVerification } from '../api';
import { useAuth } from '../auth';
import { day } from '../format';
import { Banner, errorText, StatusPill, useLoad } from './ui';

const ACCEPT = 'application/pdf,image/jpeg,.pdf,.jpg,.jpeg';

export function fileSize(n: number): string {
  return n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** One licence file with an "Open" link (signed link, new tab). */
export function LicenceFileRow({ file, link, onRemove }: { file: LicenceFile; link: () => Promise<{ url: string }>; onRemove?: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <li className="row spread" style={{ gap: 8 }}>
      <span style={{ minWidth: 0 }}>
        <span className="pdf-badge">{file.contentType === 'application/pdf' ? 'PDF' : 'JPEG'}</span> <strong>{file.filename}</strong>{' '}
        <small className="muted">
          {fileSize(file.sizeBytes)} · {day(file.uploadedAt)}
        </small>
        {err && <small className="muted"> · {err}</small>}
      </span>
      <span className="row" style={{ gap: 6 }}>
        <button type="button" className="btn ghost small" onClick={() => openSignedLink(link).catch((e) => setErr(errorText(e)))}>
          Open
        </button>
        {onRemove && (
          <button type="button" className="btn danger small" onClick={onRemove}>
            Remove
          </button>
        )}
      </span>
    </li>
  );
}

/** Doctors and clinics: verification status, the reason for a rejection, licence documents and resubmission. */
export function LicenceSection() {
  const { reload: reloadAuth } = useAuth();
  const [v, err, reload] = useLoad(() => api<MyVerification>('GET', '/v1/me/verification'), []);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      reload();
      await reloadAuth();
      setMsg({ kind: 'ok', text: ok });
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  if (err) return <Banner kind="error">{err}</Banner>;
  if (!v) return <p className="muted">Loading…</p>;
  const room = v.maxFiles - v.files.length;
  return (
    <div className="stack">
      <div className="row">
        Status: <StatusPill status={v.status} />
      </div>
      {v.reviewNote && (
        <Banner kind="error">
          <strong>{v.status === 'revoked' ? 'Verification withdrawn' : 'Not verified'}:</strong> {v.reviewNote}
          <div style={{ marginTop: 6 }}>Fix your details or upload a clearer document, then submit again.</div>
        </Banner>
      )}
      <p className="muted" style={{ margin: 0 }}>
        Upload your registration or licence certificate (PDF or JPEG, up to {v.maxFiles} files). CardiosensAI checks it before you can see patient data. Only you and CardiosensAI staff can open it.
      </p>
      {v.files.length > 0 ? (
        <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0, gap: 8 }}>
          {v.files.map((f) => (
            <LicenceFileRow
              key={f.id}
              file={f}
              link={() => api('POST', `/v1/me/verification/files/${f.id}/link`)}
              onRemove={v.status === 'verified' ? undefined : () => run(() => api('DELETE', `/v1/me/verification/files/${f.id}`), `Removed ${f.filename}.`)}
            />
          ))}
        </ul>
      ) : (
        <Banner kind="warn">No document uploaded yet. Your account can’t be verified without one.</Banner>
      )}
      {room > 0 && (
        <div className="row">
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []).slice(0, room);
              e.target.value = '';
              if (!files.length) return;
              const form = new FormData();
              files.forEach((f) => form.append('files', f));
              void run(() => apiUpload('/v1/me/verification/files', form), `Uploaded ${files.length} file${files.length > 1 ? 's' : ''}.`);
            }}
          />
          <button type="button" className="btn ghost" disabled={busy} onClick={() => input.current?.click()}>
            Upload document…
          </button>
          <small className="muted">{room} more allowed</small>
        </div>
      )}
      {v.canResubmit && (
        <div>
          <button type="button" className="btn primary" disabled={busy} onClick={() => run(() => api('POST', '/v1/me/verification/resubmit'), 'Sent for review again.')}>
            Submit again for review
          </button>
        </div>
      )}
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
    </div>
  );
}
