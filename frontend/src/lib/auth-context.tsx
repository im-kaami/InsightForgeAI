"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { auth, refreshAccessToken, type User } from "./api";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  loginWithToken: (token: string) => Promise<void>;
  setUser: (user: User) => void;
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
      // Closing the browser keeps the refresh cookie, so a missing or expired token is renewed first.
      // "if_session" only records that this browser signed in before, so visitors who never did do not
      // make a pointless refresh request.
      let current = localStorage.getItem("if_token");
      if (!current && localStorage.getItem("if_session")) current = await refreshAccessToken();
      if (current) {
        try {
          const me = await auth.me();
          if (!active) return;
          current = localStorage.getItem("if_token");
          setUser(me);
        } catch {
          localStorage.removeItem("if_token");
          current = null;
        }
      }
      if (!active) return;
      setToken(current);
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);
  async function login(email: string, password: string) {
    const result = await auth.login(email, password);
    localStorage.setItem("if_token", result.access_token);
    localStorage.setItem("if_session", "1");
    setToken(result.access_token);
    setUser(await auth.me());
  }
  async function loginWithToken(value: string) {
    localStorage.setItem("if_token", value);
    localStorage.setItem("if_session", "1");
    setToken(value);
    setUser(await auth.me());
  }
  async function register(email: string, password: string) {
    await auth.register(email, password);
    await login(email, password);
  }
  function logout() {
    void auth.logout().catch(() => undefined);
    localStorage.removeItem("if_token");
    localStorage.removeItem("if_session");
    setToken(null);
    setUser(null);
    router.push("/login");
  }
  return (
    <AuthContext.Provider
      value={{ user, token, loading, login, loginWithToken, setUser, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
