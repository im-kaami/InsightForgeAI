"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { auth, type User } from "./api";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
};
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  useEffect(() => {
    let active = true;
    Promise.resolve().then(async () => {
      const stored = localStorage.getItem("if_token");
      if (!active) return;
      setToken(stored);
      if (stored) {
        try {
          setUser(await auth.me());
        } catch {
          localStorage.removeItem("if_token");
        }
      }
      if (active) setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);
  async function login(email: string, password: string) {
    const result = await auth.login(email, password);
    localStorage.setItem("if_token", result.access_token);
    setToken(result.access_token);
    setUser(await auth.me());
  }
  async function register(email: string, password: string) {
    await auth.register(email, password);
    await login(email, password);
  }
  function logout() {
    localStorage.removeItem("if_token");
    setToken(null);
    setUser(null);
    router.push("/login");
  }
  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
