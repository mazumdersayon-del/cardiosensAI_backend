import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth';
import Layout from './components/Layout';
import { AdminUsers, AdminVerification } from './pages/Admin';
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage, VerifyEmailPage } from './pages/AuthPages';
import LinksPage from './pages/LinksPage';
import PatientHome from './pages/PatientHome';
import { ClinicDoctors, PatientDetail, ProDashboard } from './pages/ProDashboard';
import Settings from './pages/Settings';

/** "/" is the role's home when signed in, otherwise the common sign-in page. */
function Home() {
  const { user } = useAuth();
  if (!user) return <LoginPage />;
  if (user.role === 'patient') return <PatientHome />;
  if (user.role === 'admin') return <AdminVerification />;
  return <ProDashboard />;
}

export default function App() {
  const { ready, user } = useAuth();
  if (!ready)
    return (
      <div className="auth-wrap">
        <p className="muted">Loading…</p>
      </div>
    );
  const signedIn = (el: React.ReactNode) => (user ? el : <Navigate to="/" replace />);
  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/register" element={user ? <Navigate to="/" replace /> : <RegisterPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      {user ? (
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/links" element={user.role === 'admin' ? <Navigate to="/" /> : <LinksPage />} />
          <Route path="/doctors" element={user.role === 'clinic' ? <ClinicDoctors /> : <Navigate to="/" />} />
          <Route path="/patients/:patientId" element={user.role === 'doctor' || user.role === 'clinic' ? <PatientDetail /> : <Navigate to="/" />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/admin/users" element={user.role === 'admin' ? <AdminUsers /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      ) : (
        <>
          <Route path="/" element={<Home />} />
          <Route path="*" element={signedIn(null)} />
        </>
      )}
    </Routes>
  );
}
