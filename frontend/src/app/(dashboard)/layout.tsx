"use client";

import { useAuth } from "@/lib/auth";
import { FibokiLogo } from "@/components/FibokiLogo";
import { LoadingScreen } from "@/components/LoadingScreen";
import {
  BarChart3,
  Bot,
  ChartCandlestick,
  History,
  LayoutDashboard,
  LogOut,
  Search,
  Settings,
  Server,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/charts", label: "Charts", icon: ChartCandlestick },
  { href: "/backtests", label: "Backtests", icon: BarChart3 },
  { href: "/research", label: "Research", icon: Search },
  { href: "/bots", label: "Paper Bots", icon: Bot },
  { href: "/trades", label: "Trade History", icon: History },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/system", label: "System", icon: Server },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, logout, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

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
      <aside className="w-56 bg-background-card border-r border-gray-200 flex flex-col">
        <div className="px-4 py-4 border-b border-gray-200">
          <FibokiLogo size={32} />
        </div>
        <nav className="flex-1 py-4 space-y-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 px-4 py-2 text-sm transition ${
                  active
                    ? "bg-primary/10 text-primary font-medium border-r-2 border-primary"
                    : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
                }`}
              >
                <Icon size={18} />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-foreground-muted">{user?.username}</span>
            <button onClick={handleLogout} className="text-foreground-muted hover:text-danger">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 p-6 overflow-auto">{children}</main>
    </div>
  );
}
