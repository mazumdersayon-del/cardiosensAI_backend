import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth, useTheme } from '../auth';
import { formatId, ROLE_LABEL } from '../format';
import Avatar from './Avatar';
import { useUnread } from './Messages';
import SiteFooter from './SiteFooter';

/** `badge`: show the unread messages count on this item. */
const NAV: Record<string, { to: string; label: string; badge?: boolean }[]> = {
  patient: [
    { to: '/', label: 'Home' },
    { to: '/doctors', label: 'Doctors' },
    { to: '/links', label: 'Care team', badge: true },
  ],
  doctor: [
    { to: '/', label: 'Home' },
    { to: '/patients', label: 'Patients', badge: true },
    { to: '/statistics', label: 'Statistics' },
    { to: '/links', label: 'Links' },
  ],
  clinic: [
    { to: '/', label: 'Patients' },
    { to: '/doctors', label: 'Doctors' },
    { to: '/links', label: 'Links' },
  ],
  admin: [
    { to: '/admin', label: 'Overview' },
    { to: '/admin/verification', label: 'Verification' },
    { to: '/admin/users', label: 'Users' },
    { to: '/admin/devices', label: 'Devices' },
  ],
};

/** The brand line under every CardiosensAI logo. */
export function Tagline() {
  return <span className="tagline">Real Time · Real Hope</span>;
}

export function ThemeButton({ className = 'icon-btn' }: { className?: string }) {
  const [, toggle] = useTheme();
  return (
    <button type="button" className={className} onClick={toggle} aria-label="Switch light or dark theme" title="Light / dark">
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" />
      </svg>
    </button>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const unread = useUnread(user?.role === 'patient' || user?.role === 'doctor');
  if (!user) return null;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            <img src="/logos/cardiosensai-logo.png" alt="CardiosensAI home" />
            <Tagline />
          </NavLink>
          <nav className="topnav" aria-label="Main">
            {NAV[user.role].map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === '/' || n.to === '/admin'}>
                {n.label}
                {n.badge && unread > 0 && (
                  <span className="nav-badge" aria-label={`${unread} unread message${unread > 1 ? 's' : ''}`}>
                    {unread}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="who">
            <div>{user.fullName || user.email}</div>
            <div className="mono">
              {ROLE_LABEL[user.role]} · {formatId(user.uniqueId)}
            </div>
          </div>
          <Avatar user={user} size={36} />
          <NavLink to="/settings" className="icon-btn account-link" aria-label="Account" title="Account">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </NavLink>
          <ThemeButton />
          <button
            type="button"
            className="icon-btn"
            onClick={async () => {
              await logout();
              nav('/');
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <SiteFooter />
    </>
  );
}
