# الأدوار وواجهة المستخدم — Role-Based UI (Track B / Part 11)

_Shipped 2026-09-25 · owner-approved Track B · "HIDE, don't disable" +
"404, not 403" are the two iron rules._


## The five roles (11.1)

| Role | Who | Decided by (server-verified only) |
|---|---|---|
| **owner** | HmamK / the machine | `AALI_API_KEY` master key holder, or local single-user mode (no key configured = the owner's PC) |
| **dev** | builders with extra surfaces | signed-in email in `AALI_DEV_EMAILS` |
| **admin** | team admins | signed-in email in `AALI_ADMIN_EMAILS` (accounts.py promotes at login too) |
| **user** | friends / issued keys | any other authenticated account or key |
| **guest** | tunnel/LAN strangers | remote + unauthenticated |

Resolution lives in `file_agent/file_agent/roles.py::resolve_role()` — a
PURE function: it receives only server-verified facts (master-key match,
issued-key auth, account session, loopback + no `X-Forwarded-For`) and
can never see a client-sent role hint. `X-Role` headers are ignored by
construction. Ranking: `guest < user < admin < dev < owner`.

Output: `{role, user_id, workspace_id, permissions}` — permissions are a
single source of truth consumed by routing (`_require_role`) and the web
role store. Guest permissions: `chat`, `help` — nothing else.

`app.py::_resolve_role()` gathers the facts per request; `app.py::
_require_role(permission)` returns a uniform **404** when the caller
lacks the permission.

## API response rules (11.2)

- **404 (not 401/403)** for unauthorized access — a probe must never
  learn the surface exists. Applies to the before-request key gate, all
  `/api/admin/*`, `/api/auth/handoff`, guest search, `/brain`.
- `/api/health` stays OPEN (watchdog contract, 2026-09-23) — body has
  service/workspace/mode only, never user content.
- `/api/sessions`: owner sees every session; users/keys see only their
  own namespace — enforced server-side.
- Guest chat tools stay blocked by the guest policy (unchanged layer).

## Role store (11.3) + conditional rendering (11.4) + sidebar (11.5)

`web/src/store/role.tsx`: `RoleProvider` fetches `/api/auth/me` on load,
exposes `useRole()` → `{role, ctx, isOwner, isDev, isAdmin, isUser,
isGuest, can}` (`can` = permission check against the server-sent list).
Every role-gated component uses `<IfRole>` / `useRole().can(...)` —
elements are NOT RENDERED (HIDE, don't disable), never merely grayed out.

Sidebar (Arabic-first labels):
- **owner**: المحادثات، المشاريع، المساعدون، النماذج، التدريب، التحليلات،
  التقارير، السجلات، الأمان، الإدارة، الإعدادات
- **admin**: المحادثات، المشاريع، المساعدون، المستخدمون، التحليلات،
  الإعدادات، المساعدة
- **user**: المحادثات، المشاريع، المساعدون، الإعدادات، المساعدة
- **guest**: المحادثات، المساعدة

The admin pill (شارة «فريق») and the 📊 / ⚙️ header controls render only
when the server says the caller may see them.

## Files

- `file-agent/file_agent/roles.py` (new) — resolver + permissions
- `file-agent/app.py` — `_resolve_role`, `_require_role`, role endpoint,
  404 contract, owner-sees-all sessions
- `web/src/store/role.tsx` (new), `web/src/App.tsx`, `web/src/styles.css`
- `tests/test_roles.py` (new), plus 404-contract updates in
  `test_auth_http.py`, `test_export.py`, `test_search.py`,
  `test_brain.py`, `test_hwk.py`

## Tests

`tests/test_roles.py` (19): every promotion path (master key, local
no-key, dev/admin env emails — case-insensitive, issued keys, remote
guest), the "local + key configured but anonymous ≠ owner" trap,
ranking helpers, permissions shape, and the HTTP contract of
`/api/auth/me` (owner via master key, admin via email, user via issued
key, 404 anonymous in key mode, owner context in local mode).
Suite: 911 passed / 9 skipped.
