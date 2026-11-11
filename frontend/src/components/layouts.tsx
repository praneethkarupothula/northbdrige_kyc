import { ClipboardList, Gauge, LogOut, Settings, ShieldAlert, SlidersHorizontal, Users } from "lucide-react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "@/components/brand";
import { NotificationBell } from "@/components/notifications";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { useLogout, useMe } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

function useGuard(portal: "applicant" | "staff") {
  const me = useMe();
  const loc = useLocation();
  if (me.isLoading) return { el: <div className="p-8"><Skeleton className="h-40" /></div> };
  const u = me.data;
  if (!u || u.portal !== portal) {
    return { el: <Navigate to={portal === "staff" ? "/staff/login" : "/login"} replace state={{ from: loc.pathname }} /> };
  }
  return { user: u };
}

function LogoutButton({ to }: { to: string }) {
  const logout = useLogout();
  const nav = useNavigate();
  return (
    <Button variant="ghost" size="sm" onClick={() => logout.mutate(undefined, { onSettled: () => nav(to) })}>
      <LogOut /> <span className="hidden sm:inline">Sign out</span>
    </Button>
  );
}

const navCls = ({ isActive }: { isActive: boolean }) =>
  cn("inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm", isActive ? "bg-secondary font-medium" : "text-muted-foreground hover:text-foreground");

export function ApplicantLayout() {
  const g = useGuard("applicant");
  if (!g.user) return g.el;
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-card focus:p-2">Skip to content</a>
      <header className="border-b bg-card/70">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-2 px-4 py-3">
          <Logo to="/app" />
          <nav className="flex items-center gap-1" aria-label="Account">
            <NavLink to="/app" end className={navCls}>Dashboard</NavLink>
            <NavLink to="/app/settings" className={navCls}><Settings className="size-4" /><span className="hidden sm:inline">Settings</span></NavLink>
            <NotificationBell />
            <LogoutButton to="/login" />
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 py-6"><Outlet /></main>
    </div>
  );
}

export function StaffLayout() {
  const g = useGuard("staff");
  if (!g.user) return g.el;
  const admin = g.user.role === "admin";
  return (
    <div className="min-h-screen bg-primary/[0.03] lg:grid lg:grid-cols-[220px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-card focus:p-2">Skip to content</a>
      <aside className="border-b bg-card lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between gap-2 p-4 lg:block">
          <Logo to="/staff" sub="Staff portal" />
          <div className="flex items-center gap-1 lg:hidden"><NotificationBell /><LogoutButton to="/staff/login" /></div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col" aria-label="Staff">
          <NavLink to="/staff" end className={navCls}><Gauge className="size-4" />Overview</NavLink>
          <NavLink to="/staff/queue" className={navCls}><ClipboardList className="size-4" />Review queue</NavLink>
          {admin && <>
            <p className="hidden px-3 pt-4 text-xs text-muted-foreground lg:block">Administration</p>
            <NavLink to="/staff/admin/staff" className={navCls}><Users className="size-4" />Staff</NavLink>
            <NavLink to="/staff/admin/policy" className={navCls}><SlidersHorizontal className="size-4" />Policy</NavLink>
            <NavLink to="/staff/admin/conflicts" className={navCls}><ShieldAlert className="size-4" />Identity conflicts</NavLink>
          </>}
        </nav>
        <div className="hidden px-4 pt-6 text-xs text-muted-foreground lg:block">
          <p>{g.user.full_name}</p><p className="capitalize">{g.user.role}</p>
          <div className="mt-2 -ml-3 flex items-center"><NotificationBell /><LogoutButton to="/staff/login" /></div>
        </div>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 lg:px-8"><Outlet /></main>
    </div>
  );
}
