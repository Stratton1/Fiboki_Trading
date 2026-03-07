"use client";

import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">
        Welcome to FIBOKEI{user ? `, ${user.username}` : ""}
      </h2>
      <p className="text-foreground-muted">Dashboard with KPIs and analytics coming next.</p>
    </div>
  );
}
