import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth, useTheme } from '../auth';
import { formatId, ROLE_LABEL } from '../format';

const NAV: Record<string, { to: string; label: string }[]> = {
  patient: [
    { to: '/', label: 'Home' },
    { to: '/links', label: 'Care team' },
    { to: '/settings', label: 'Account' },
  ],
  doctor: [
    { to: '/', label: 'Patients' },
    { to: '/links', label: 'Links' },
    { to: '/settings', label: 'Account' },
  ],
  clinic: [
    { to: '/', label: 'Patients' },
    { to: '/doctors', label: 'Doctors' },
    { to: '/links', label: 'Links' },
    { to: '/settings', label: 'Account' },
  ],
  admin: [
    { to: '/', label: 'Verification' },
    { to: '/admin/users', label: 'Users' },
    { to: '/settings', label: 'Account' },
  ],
};

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
  if (!user) return null;
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            <img src="/logos/cardiosensai-logo.png" alt="CardiosensAI home" />
          </NavLink>
          <nav className="topnav" aria-label="Main">
            {NAV[user.role].map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === '/'}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="who">
            <div>{user.fullName || user.email}</div>
            <div className="mono">
              {ROLE_LABEL[user.role]} · {formatId(user.uniqueId)}
            </div>
          </div>
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
    </>
  );
}
