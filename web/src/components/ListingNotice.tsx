import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type DoctorListing } from '../api';
import { useAuth } from '../auth';
import { Banner, errorText, useLoad } from './ui';

/*
 * Doctor Home: says when patients can't find this doctor in their Doctors list, and why. Patients only see doctors who
 * opted in (off by default), are verified, active and on two-step sign-in (api/v1/doctors.py `_listed`), and nothing
 * told the doctor which one was missing. Not opted in: one click opts in (or "Not now", remembered on this browser).
 */

const HIDE_KEY = 'cs.listingNotice.hidden.';

function hiddenHere(uid: string): boolean {
  try {
    return localStorage.getItem(HIDE_KEY + uid) === '1';
  } catch {
    return false;
  }
}

/** Where to fix a hidden listing (reasons from `_listing_out`). */
function fixLink(reason: string): { to: string; label: string } {
  if (reason.includes('two-step')) return { to: '/settings#security', label: 'Turn on two-step sign-in' };
  if (reason.includes('verified')) return { to: '/settings#documents', label: 'Check your verification' };
  return { to: '/settings#listing', label: 'Open Doctors list settings' };
}

export function ListingNotice() {
  const { user } = useAuth();
  const [l, , reload] = useLoad(() => api<DoctorListing>('GET', '/v1/me/doctor-listing').catch(() => null), []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hidden, setHidden] = useState(() => !!user && hiddenHere(user.uniqueId ?? user.id));
  if (!l || l.visible || !user) return null;

  if (l.listed && l.hiddenReason) {
    const fix = fixLink(l.hiddenReason);
    return (
      <Banner kind="warn">
        <strong>Patients can’t see you in the Doctors list yet.</strong> {l.hiddenReason} <Link to={fix.to}>{fix.label}</Link>
      </Banner>
    );
  }
  if (l.listed || hidden) return null;

  const optIn = async () => {
    setBusy(true);
    setErr(null);
    try {
      // the listing is saved whole: send the current card with "listed" ticked
      const { visible: _v, hiddenReason: _h, specialty: _s, ...card } = l;
      await api('PUT', '/v1/me/doctor-listing', { ...card, listed: true });
      reload();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const notNow = () => {
    try {
      localStorage.setItem(HIDE_KEY + (user.uniqueId ?? user.id), '1');
    } catch {
      /* private window: hidden until the page reloads */
    }
    setHidden(true);
  };
  return (
    <div className="banner info listing-notice" role="status">
      <div>
        <strong>Patients can’t find you in the Doctors list</strong>
        <div className="small">
          You haven’t chosen to appear there. Patients then see your name, picture, specialty, council and the card details from Account → Doctors list,
          never your mobile number or email.
        </div>
        {err && <div className="small">{err}</div>}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn primary small" disabled={busy} onClick={() => void optIn()}>
          {busy ? 'Saving…' : 'Show me to patients'}
        </button>
        <Link className="btn ghost small" to="/settings#listing">
          Edit my card
        </Link>
        <button type="button" className="btn ghost small" onClick={notNow}>
          Not now
        </button>
      </div>
    </div>
  );
}
