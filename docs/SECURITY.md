# Security and access

## The defect this replaced

Authentication was previously enabled by a derived property:

```python
@property
def auth_enabled(self) -> bool:
    return bool(self.app_username and self.app_password)
```

Absent credentials therefore **disabled** authentication rather than denying
access. The Railway deployment had no credentials set, so every route was
public, including the linked manager's name, team name and entry ID, the data
exports, and the `POST /admin/refresh` endpoint. `valid_credentials` compounded
it by returning `True` when nothing was configured.

Access is now decided by an explicit setting that defaults to closed, and
`valid_credentials` returns `False` when no credentials exist.

## Access modes

Set `ACCESS_MODE` to one of:

| Mode | Analytics pages | Personal pages | Mutations | Intended for |
| --- | --- | --- | --- | --- |
| `demo` (default) | Public | Sign-in required | Sign-in required | A shareable deployment. Market data is impersonal; nothing about the linked team is reachable. |
| `private` | Sign-in required | Sign-in required | Sign-in required | Tailscale, LAN, or any deployment that should not be readable by strangers. |
| `local` | Open | Open | Open | Local development only. **Refuses to start unless the database is SQLite**, so it cannot be selected by accident in production. |

If a mode requires credentials and none are configured, the application still
starts and still serves its public class, but every personal and mutation route
is refused and the login page says so. Starting beats crash-looping on Railway,
and the security property holds either way.

## Protection classes

Routes are classified in `app/web/auth.py`. Anything unlisted is `ANALYTICS`,
so a new route is public in demo mode by default — acceptable because analytics
data is impersonal, and anything personal must be added to the map explicitly.

| Class | Members |
| --- | --- |
| `PUBLIC` | `/health`, `/login`, `/logout`, `/static` |
| `ANALYTICS` | Dashboard, spreadsheet, movers, differentials, transfer market, templates, comparison, exports |
| `PERSONAL` | `/my-team` |
| `MUTATION` | `/admin/*` |

## Privacy by exclusion

Personal data is not masked at render time; it is never fetched for an
unauthenticated request. `linked_team_data()` is called only when the session is
authenticated, so the manager name and entry ID never enter a template context
an anonymous visitor can receive. Masking depends on every template applying a
filter correctly; exclusion does not.

## Other controls

- **Session cookies** are `HttpOnly` always, `Secure` when the database is
  PostgreSQL (i.e. in production), `SameSite=Lax`, and expire after
  `SESSION_MAX_AGE_SECONDS` (default 12 hours).
- **CSRF** tokens are required on every state-changing form.
- **Login throttling** blocks after `LOGIN_MAX_ATTEMPTS` failures within
  `LOGIN_LOCKOUT_SECONDS`. It is per-process and resets on restart, so it slows
  guessing rather than defeating it — use a strong password.
- **Audit log** (`audit_events`) records login success, login failure, throttling,
  logout and every mutation, with actor, path, client address and timestamp.
  Passwords, CSRF tokens and API keys are redacted before storage.

Query it with:

```sql
SELECT occurred_at, action, actor, path, client
FROM audit_events ORDER BY occurred_at DESC LIMIT 50;
```

## Railway

Set these variables on the service:

```
ACCESS_MODE=demo            # or private
APP_USERNAME=<your username>
APP_PASSWORD=<a long random password>
SESSION_SECRET=<a long random value, not the default>
CURRENT_SEASON=2026/27
```

`SESSION_SECRET` must be changed from its default. The default is a known
string, and anyone holding it can forge a session cookie.

## Tailscale

Run with `ACCESS_MODE=private` and do not expose the container port publicly.

1. Install Tailscale on the host and join your tailnet.
2. Bind the app to the host only: publish `127.0.0.1:8000:8000` rather than
   `8000:8000` in Docker Compose.
3. Serve it over the tailnet with TLS termination:

   ```bash
   tailscale serve --bg 8000
   ```

4. Reach it at `https://<machine>.<tailnet>.ts.net`.

Keep `ACCESS_MODE=private` even on a tailnet. Tailscale controls who can reach
the port; it does not control who is sitting at an authorised device.

## Reporting

There is no automated dependency scanning in this repository yet. Before a
public deployment, review installed package versions and rotate
`SESSION_SECRET`, `APP_PASSWORD` and any FPL credentials.
