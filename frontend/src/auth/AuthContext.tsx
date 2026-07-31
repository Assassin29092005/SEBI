// Real auth state, replacing the old demo-grade role dropdown. Holds the
// bearer token + the logged-in user, persists the token across reloads, and
// listens for "auth:unauthorized" (dispatched by api/client on any 401) so
// an expired/invalid token bounces the whole app back to the login screen.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  getMe,
  getToken,
  login as apiLogin,
  register as apiRegister,
  setToken as persistToken,
  type LoginInput,
  type RegisterInput,
  type UserPublic,
} from "../api/client";

interface AuthState {
  user: UserPublic | null;
  loading: boolean; // true only during the initial token → user check
  login: (input: LoginInput) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const logout = useCallback(() => {
    persistToken(null);
    setUser(null);
  }, []);

  // On mount: if a token survived a reload, validate it against /api/auth/me
  // rather than trusting it blindly (it may have expired since the last visit).
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    getMe()
      .then(setUser)
      .catch(() => {
        persistToken(null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  // Any 401 from the API layer means the token is dead — drop to logged-out.
  useEffect(() => {
    const onUnauthorized = () => setUser(null);
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  const login = useCallback(async (input: LoginInput) => {
    const result = await apiLogin(input);
    persistToken(result.access_token);
    setUser(result.user);
  }, []);

  const register = useCallback(async (input: RegisterInput) => {
    const result = await apiRegister(input);
    persistToken(result.access_token);
    setUser(result.user);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
