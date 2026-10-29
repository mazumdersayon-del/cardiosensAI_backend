import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth';
import Layout from './components/Layout';
import { DoctorHome, DoctorStats, PatientAppointments } from './pages/Appointments';
import { AdminDevices, AdminOverview, AdminUsers, AdminVerification } from './pages/Admin';
import { ForgotPasswordPage, LoginCodePage, LoginPage, RegisterCodePage, RegisterPage, ResetPasswordPage } from './pages/AuthPages';
import DoctorsPage from './pages/Doctors';
import LinksPage from './pages/LinksPage';
import PatientHome from './pages/PatientHome';
import { ClinicDoctors, PatientDetail, ProDashboard } from './pages/ProDashboard';
import Settings from './pages/Settings';
import { MessagesPage } from './components/Messages';
import { AboutYouPage } from './components/PatientDetails';

/** "/" is the role's home when signed in, otherwise the common sign-in page. */
function Home() {
  const { user } = useAuth();
  if (!user) return <LoginPage />;
  if (user.role === 'patient') return <PatientHome />;
  if (user.role === 'admin') return <Navigate to="/admin" replace />;
  if (user.role === 'doctor') return <DoctorHome />;
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
      <Route path="/register/verify" element={user ? <Navigate to="/" replace /> : <RegisterCodePage />} />
      <Route path="/login/code" element={user ? <Navigate to="/" replace /> : <LoginCodePage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      {user ? (
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/links" element={user.role === 'admin' ? <Navigate to="/" /> : <LinksPage />} />
          <Route path="/doctors" element={user.role === 'clinic' ? <ClinicDoctors /> : user.role === 'patient' ? <DoctorsPage /> : <Navigate to="/" />} />
          <Route path="/patients" element={user.role === 'doctor' ? <ProDashboard /> : <Navigate to="/" replace />} />
          <Route path="/statistics" element={user.role === 'doctor' ? <DoctorStats /> : <Navigate to="/" replace />} />
          <Route path="/appointments" element={user.role === 'patient' ? <PatientAppointments /> : <Navigate to="/" replace />} />
          <Route path="/messages" element={user.role === 'patient' ? <MessagesPage /> : <Navigate to={user.role === 'doctor' ? '/patients' : '/'} replace />} />
          <Route path="/messages/:party" element={user.role === 'patient' ? <MessagesPage /> : <Navigate to={user.role === 'doctor' ? '/patients' : '/'} replace />} />
          <Route path="/patients/:patientId" element={user.role === 'doctor' || user.role === 'clinic' ? <PatientDetail /> : <Navigate to="/" />} />
          <Route path="/devices" element={<Navigate to={user.role === 'admin' ? '/' : '/settings#devices'} replace />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/about-you" element={user.role === 'patient' ? <AboutYouPage /> : <Navigate to="/" replace />} />
          <Route path="/admin" element={user.role === 'admin' ? <AdminOverview /> : <Navigate to="/" />} />
          <Route path="/admin/verification" element={user.role === 'admin' ? <AdminVerification /> : <Navigate to="/" />} />
          <Route path="/admin/users" element={user.role === 'admin' ? <AdminUsers /> : <Navigate to="/" />} />
          <Route path="/admin/devices" element={user.role === 'admin' ? <AdminDevices /> : <Navigate to="/" />} />
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
