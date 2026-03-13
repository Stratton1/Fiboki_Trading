"use client";

import { useAuth } from "@/lib/auth";
import { FibokiLogo } from "@/components/FibokiLogo";
import { ExecutionModeBanner } from "@/components/ExecutionModeBanner";
import { LoadingScreen } from "@/components/LoadingScreen";
import {
  BarChart3,
  Bell,
  Bot,
  ChartCandlestick,
  History,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Layers,
  Search,
  Settings,
  Server,
  Shield,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/charts", label: "Charts", icon: ChartCandlestick },
  { href: "/backtests", label: "Backtests", icon: BarChart3 },
  { href: "/research", label: "Research", icon: Search },
  { href: "/scenarios", label: "Scenarios", icon: Layers },
  { href: "/jobs", label: "Jobs", icon: ListTodo },
  { href: "/bots", label: "Paper Bots", icon: Bot },
  { href: "/exposure", label: "Exposure", icon: Shield },
  { href: "/trades", label: "Trade History", icon: History },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/system", label: "System", icon: Server },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, logout, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const { data: jobsData } = useSWR("/jobs/active-count", () => api.listJobs("limit=1"), {
    refreshInterval: 5000,
  });
  const activeJobCount = jobsData?.active_count ?? 0;
  const { data: alertData } = useSWR("/alerts/unread-count", () => api.unreadAlertCount(), {
    refreshInterval: 30000,
  });
  const unreadAlertCount = alertData?.unread_count ?? 0;

  if (isLoading) {
    return <LoadingScreen />;
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <div className="min-h-screen flex bg-background">
      {/* Sidebar */}
      <aside className="w-60 bg-background-card border-r border-border flex flex-col shrink-0">
        <div className="px-5 py-5 border-b border-border">
          <FibokiLogo size={32} />
        </div>
        <nav className="flex-1 py-3 px-3 space-y-0.5">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg transition-all ${
                  active
                    ? "bg-primary/10 text-primary font-medium shadow-sm"
                    : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
                }`}
              >
                <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
                {label}
                {href === "/jobs" && activeJobCount > 0 && (
                  <span className="ml-auto text-xs bg-primary text-white rounded-full px-1.5 py-0.5 min-w-[18px] text-center leading-none">
                    {activeJobCount}
                  </span>
                )}
                {href === "/alerts" && unreadAlertCount > 0 && (
                  <span className="ml-auto text-xs bg-amber-500 text-white rounded-full px-1.5 py-0.5 min-w-[18px] text-center leading-none">
                    {unreadAlertCount > 99 ? "99+" : unreadAlertCount}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-border p-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary text-xs font-bold uppercase">
              {user?.username?.charAt(0) ?? "?"}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{user?.username}</p>
              <p className="text-xs text-foreground-muted">{user?.role ?? "operator"}</p>
            </div>
            <button
              onClick={handleLogout}
              className="text-foreground-muted hover:text-danger transition-colors p-1 rounded-md hover:bg-red-50"
              title="Sign out"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 p-8 overflow-auto">
        <div className="max-w-7xl">
          <ExecutionModeBanner />
          {children}
        </div>
      </main>
    </div>
  );
}
