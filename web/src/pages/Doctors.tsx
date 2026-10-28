import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Doctor, type DoctorCard } from '../api';
import { useAuth } from '../auth';
import { Picture } from '../components/Avatar';
import { Banner, errorText } from '../components/ui';
import { formatId } from '../format';

const PAGE = 24;

function years(n: number | null) {
  return n ? `${n} year${n === 1 ? '' : 's'} experience` : null;
}

/** Patients: every verified doctor who chose to be listed, as cards; a card opens the full profile. */
export default function DoctorsPage() {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<DoctorCard[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async (search: string, offset: number) => {
    setBusy(true);
    setErr(null);
    try {
      const page = await api<{ items: DoctorCard[]; total: number }>('GET', `/v1/doctors?limit=${PAGE}&offset=${offset}${search ? `&q=${encodeURIComponent(search)}` : ''}`);
      setItems((old) => (offset ? [...old, ...page.items] : page.items));
      setTotal(page.total);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    void load(query, 0);
  }, [query, load]);

  // more cards as the list scrolls to its end
  const more = total !== null && items.length < total;
  useEffect(() => {
    const el = end.current;
    if (!el || !more || busy) return;
    const io = new IntersectionObserver((e) => e[0]?.isIntersecting && void load(query, items.length), { rootMargin: '400px' });
    io.observe(el);
    return () => io.disconnect();
  }, [more, busy, items.length, query, load]);

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="doctors-head">
        <div>
          <h1>Doctors</h1>
          <p className="muted">Doctors verified by CardiosensAI who chose to be listed. Open a card to see their profile and send a link request.</p>
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, specialty, hospital or city" aria-label="Search doctors" />
      </div>
      {err && <Banner kind="error">{err}</Banner>}
      {total === 0 && !busy && <p className="muted">{query ? `No doctors match “${query}”.` : 'No doctors are listed yet.'}</p>}
      <ul className="doctor-grid">
        {items.map((d) => (
          <li key={d.uniqueId}>
            <button type="button" className="doctor-card" onClick={() => setOpen(d.uniqueId)} aria-label={`${d.fullName}, ${d.specialty ?? 'doctor'}. Open profile`}>
              <Picture path={`/v1/doctors/${d.uniqueId}/avatar`} version={d.avatarVersion} name={d.fullName} size={72} />
              <span className="doctor-name">{d.fullName}</span>
              {d.specialty && <span className="doctor-spec">{d.specialty}</span>}
              {d.degrees && <span className="doctor-deg">{d.degrees}</span>}
              <span className="doctor-meta">{[years(d.experienceYears), [d.practice, d.city].filter(Boolean).join(', ')].filter(Boolean).join(' · ')}</span>
            </button>
          </li>
        ))}
      </ul>
      <div ref={end} />
      {busy && <p className="muted">Loading…</p>}
      {open && <DoctorDialog uniqueId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function DoctorDialog({ uniqueId, onClose }: { uniqueId: string; onClose: () => void }) {
  const { status } = useAuth();
  const ref = useRef<HTMLDialogElement>(null);
  const [d, setD] = useState<Doctor | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    ref.current?.showModal();
    api<Doctor>('GET', `/v1/doctors/${uniqueId}`).then(setD, (e) => setErr(errorText(e)));
  }, [uniqueId]);

  const request = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api('POST', '/v1/links', { targetId: uniqueId });
      setMsg('Link request sent. The doctor sees your readings only after they accept, and you can end the link at any time.');
      setD((x) => (x ? { ...x, linkStatus: 'pending' } : x));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="ecg-dialog doctor-dialog" onClose={onClose} onClick={(e) => e.target === e.currentTarget && ref.current?.close()} aria-label="Doctor profile">
      <div className="ecg-dialog-box">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div className="row" style={{ gap: 16, alignItems: 'center' }}>
            <Picture path={`/v1/doctors/${uniqueId}/avatar`} version={d?.avatarVersion} name={d?.fullName} size={88} />
            <div>
              <h2 style={{ margin: 0 }}>{d?.fullName ?? 'Loading…'}</h2>
              {d?.specialty && <div className="doctor-spec">{d.specialty}</div>}
              {d?.degrees && <div className="doctor-deg">{d.degrees}</div>}
            </div>
          </div>
          <button type="button" className="btn ghost small" onClick={() => ref.current?.close()}>
            Close
          </button>
        </div>
        {err && <Banner kind="error">{err}</Banner>}
        {msg && <Banner kind="ok">{msg}</Banner>}
        {d && (
          <>
            {d.about && <p style={{ margin: 0 }}>{d.about}</p>}
            <dl className="doctor-facts">
              {d.experienceYears ? (
                <>
                  <dt>Experience</dt>
                  <dd>{years(d.experienceYears)}</dd>
                </>
              ) : null}
              {d.practice || d.city ? (
                <>
                  <dt>Practises at</dt>
                  <dd>{[d.practice, d.city].filter(Boolean).join(', ')}</dd>
                </>
              ) : null}
              {d.languages && (
                <>
                  <dt>Languages</dt>
                  <dd>{d.languages}</dd>
                </>
              )}
              <dt>Registration</dt>
              <dd>
                {d.council} · <span className="pill ok">Verified by CardiosensAI</span>
              </dd>
              <dt>CardiosensAI ID</dt>
              <dd className="mono">{formatId(d.uniqueId)}</dd>
            </dl>
            <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
              {d.linkStatus === 'active' ? (
                <span className="pill ok">Linked: this doctor can see your readings</span>
              ) : d.linkStatus === 'pending' ? (
                <span className="pill warn">Link request waiting</span>
              ) : status?.canLink ? (
                <button type="button" className="btn primary" disabled={busy} onClick={request}>
                  {busy ? 'Sending…' : 'Send link request'}
                </button>
              ) : (
                <span className="muted">Confirm your mobile number or email to link with a doctor.</span>
              )}
              <Link to="/links" className="btn ghost">
                Care team
              </Link>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
