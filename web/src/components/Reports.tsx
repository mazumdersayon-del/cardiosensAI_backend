import { useState } from 'react';
import { api, type Report } from '../api';
import { day, VITALS, when } from '../format';
import { Banner, errorText, StatusPill, useLoad } from './ui';

export default function ReportsPanel({ patientId }: { patientId: string }) {
  const [list, err, reload] = useLoad(() => api<Report[]>('GET', `/v1/reports?patientId=${patientId}`), [patientId]);
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(today);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null);

  async function create() {
    setBusy(true);
    setMsg(null);
    try {
      await api('POST', '/v1/reports', {
        patientId,
        fromDate: from,
        toDate: to,
        vitals: VITALS.map((v) => v.key),
        includeEcg: true,
        utcOffsetMin: -new Date().getTimezoneOffset(),
      });
      setMsg({ kind: 'ok', text: 'Report requested. It appears below when it is ready (usually under a minute).' });
      reload();
      setTimeout(reload, 4000);
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  async function download(r: Report) {
    try {
      const { url } = await api<{ url: string }>('POST', `/v1/reports/${r.id}/download-link`);
      window.location.assign(url);
    } catch (e) {
      setMsg({ kind: 'error', text: errorText(e) });
    }
  }

  return (
    <div className="stack">
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: '1 1 140px' }}>
          From
          <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="field" style={{ flex: '1 1 140px' }}>
          To
          <input type="date" value={to} min={from} max={today} onChange={(e) => setTo(e.target.value)} />
        </label>
        <button type="button" className="btn primary" disabled={busy} onClick={create}>
          Create PDF report
        </button>
      </div>
      {msg && <Banner kind={msg.kind}>{msg.text}</Banner>}
      {err && <p className="muted">{err}</p>}
      {list && !list.length && <div className="empty">No reports yet.</div>}
      {list && list.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Period</th>
                <th>Requested</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.id}>
                  <td>
                    {day(r.params.fromDate)} – {day(r.params.toDate)}
                  </td>
                  <td>{when(r.createdAt)}</td>
                  <td>
                    <StatusPill status={r.status} />
                  </td>
                  <td>
                    {r.status === 'ready' ? (
                      <button type="button" className="btn ghost small" onClick={() => download(r)}>
                        Download PDF
                      </button>
                    ) : (
                      <button type="button" className="btn ghost small" onClick={reload}>
                        Refresh
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
