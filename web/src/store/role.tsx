/* ————— Aali role store (Track B 11.3, 2026-09-25) —————

Fetches the server's role verdict (/api/auth/me → aali:{role,user_id,
workspace_id,permissions}) once on load and exposes it app-wide.

Rules implemented here:
- The SERVER decides the role; the client only renders it. Nothing in
  this store ever reads localStorage/local hints to elevate a caller.
- HIDE, don't disable (11.4): components check can()/isOwner and simply
  do not render — never a disabled button teasing a hidden surface.
- 404 on /api/auth/me (key mode, anonymous) = guest view; the store
  treats any failure as the most limited role. The server re-checks
  every request anyway — the client is presentation only. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { authMe, getApiBase, getToken } from "../api";

export type AaliRole = "owner" | "dev" | "admin" | "user" | "guest";

export interface RoleContext {
  role: AaliRole;
  user_id: string;
  workspace_id: string;
  permissions: string[];
  previewing?: boolean;
}

interface RoleState {
  role: AaliRole;
  ctx: RoleContext | null;
  loaded: boolean;
  isOwner: boolean;
  isDev: boolean;
  isAdmin: boolean;
  isUser: boolean;
  isGuest: boolean;
  previewing: boolean;
  can: (permission: string) => boolean;
  refresh: () => Promise<void>;
}

const RoleContextReact = createContext<RoleState | null>(null);

function fallbackGuest(): RoleState {
  return {
    role: "guest",
    ctx: null,
    loaded: false,
    isOwner: false,
    isDev: false,
    isAdmin: false,
    isUser: false,
    isGuest: true,
    previewing: false,
    can: () => false,
    refresh: async () => undefined,
  };
}

export function RoleProvider({ children }: { children: ReactNode }) {
  const [ctx, setCtx] = useState<RoleContext | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const me = await authMe();
      const aali = (me as { aali?: RoleContext }).aali;
      if (aali && aali.role) {
        setCtx({
          role: aali.role,
          user_id: aali.user_id || "",
          workspace_id: aali.workspace_id || "",
          permissions: Array.isArray(aali.permissions) ? aali.permissions : [],
          previewing: !!aali.previewing,
        });
      } else {
        setCtx(null);
      }
    } catch {
      // 404/any failure = most limited view (guest). Server still gates.
      setCtx(null);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const state = useMemo<RoleState>(() => {
    if (!loaded) {
      // Before the verdict arrives render nothing role-gated: children
      // of the gate helpers stay hidden for one fetch round-trip.
      return { ...fallbackGuest(), refresh };
    }
    const role: AaliRole = ctx?.role ?? "guest";
    const isOwner = role === "owner";
    const isDev = role === "dev";
    const isAdmin = role === "admin";
    const isUser = role === "user";
    const isGuest = role === "guest";
    const previewing = !!ctx?.previewing;
    const perms = new Set(ctx?.permissions ?? []);
    return {
      role,
      ctx,
      loaded,
      isOwner,
      isDev,
      isAdmin,
      isUser,
      isGuest,
      previewing,
      can: (permission: string) => perms.has(permission),
      refresh,
    };
  }, [ctx, loaded, refresh]);

  return (
    <RoleContextReact.Provider value={state}>
      {children}
    </RoleContextReact.Provider>
  );
}

export function useRole(): RoleState {
  const ctx = useContext(RoleContextReact);
  if (!ctx) throw new Error("useRole must be used inside <RoleProvider>");
  return ctx;
}

/* Render children ONLY when the caller holds the permission — HIDE,
   don't disable: nothing mounts, nothing stays in the DOM. */
export function IfRole({
  permission,
  anyOf,
  children,
}: {
  permission?: string;
  anyOf?: AaliRole[];
  children: ReactNode;
}) {
  const { can, role, loaded } = useRole();
  if (!loaded) return null;
  if (anyOf && anyOf.includes(role)) return <>{children}</>;
  if (permission && can(permission)) return <>{children}</>;
  return null;
}

/* Convenience for callers that only need the API base + auth handling
   already wired: re-exported so gates stay in one import. */
export { getApiBase, getToken };
