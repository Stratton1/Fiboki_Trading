"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

interface User {
  user_id: number;
  username: string;
  role: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    try {
      const res = await api.login(username, password);
      if (!res.ok) return false;
      const data = await res.json();
      if (data.access_token) {
        localStorage.setItem("fibokei_token", data.access_token);
        // Set a frontend-domain cookie so Next.js middleware knows we're logged in
        document.cookie = "fiboki_auth=1; path=/; max-age=86400; SameSite=Lax";
      }
      const me = await api.me();
      setUser(me);
      return true;
    } catch {
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    await api.logout().catch(() => {});
    localStorage.removeItem("fibokei_token");
    document.cookie = "fiboki_auth=; path=/; max-age=0";
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
