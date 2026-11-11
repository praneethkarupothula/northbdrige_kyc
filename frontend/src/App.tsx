import { Navigate, Route, Routes } from "react-router-dom";
import { ApplicantLayout, StaffLayout } from "@/components/layouts";
import { ApplicantDashboard } from "@/pages/applicant/Dashboard";
import { SettingsPage } from "@/pages/applicant/Settings";
import { DevMailboxPage } from "@/pages/public/DevMailbox";
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage, VerifyEmailPage } from "@/pages/public/forms";
import { WelcomePage } from "@/pages/public/WelcomePage";
import { ConflictsPage, PolicyPage, StaffAdminPage } from "@/pages/staff/Admin";
import { StaffOverview } from "@/pages/staff/Overview";
import { QueuePage } from "@/pages/staff/Queue";
import { ReviewPage } from "@/pages/staff/Review";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<WelcomePage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/dev/mailbox" element={<DevMailboxPage />} />
      <Route path="/staff/login" element={<LoginPage staff />} />
      <Route path="/staff/forgot-password" element={<ForgotPasswordPage staff />} />
      <Route path="/staff/reset-password" element={<ResetPasswordPage staff />} />
      <Route path="/app" element={<ApplicantLayout />}>
        <Route index element={<ApplicantDashboard />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="/staff" element={<StaffLayout />}>
        <Route index element={<StaffOverview />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="applications/:id" element={<ReviewPage />} />
        <Route path="admin/staff" element={<StaffAdminPage />} />
        <Route path="admin/policy" element={<PolicyPage />} />
        <Route path="admin/conflicts" element={<ConflictsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
