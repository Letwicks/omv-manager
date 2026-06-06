# OMV Manager — AGENTS.md

## Project

Automated management of users, groups, folders, ACLs, and SMB shares on OpenMediaVault 8.x (Debian 13). Three implementations coexist:

- **`cli/omv-manager.py`** — CLI tool, remote via `paramiko` SSH
- **`gui/omv-gui.py`** — Tkinter GUI, imports `cli/omv-manager.py` via `importlib`
- **`web/app.py`** — Flask web app, runs directly on OMV server (no SSH)

All are **idempotent** and implement the same core flow.

## Key files

| File | Role |
|---|---|
| `cli/omv-manager.py` | CLI + remote SSH orchestration |
| `gui/omv-gui.py` | Tkinter GUI, imports `../cli/omv-manager.py` via `importlib` |
| `web/app.py` | Flask web app (runs on server, `subprocess` instead of SSH) |
| `web/localshell.py` | Drop-in replacement for SSHClient using `subprocess` |
| `web/deploy-web.py` | Deploy web app to OMV server via SSH |
| `config.yaml` | YAML config (gitignored — sensitive data) |
| `cli/config.yaml.example` | Template without real credentials |

## Commands

```bash
# CLI
pip install cli/requirements.txt
python cli/omv-manager.py --config config.yaml --apply
python cli/omv-manager.py --config config.yaml --dry-run
python cli/omv-manager.py --config config.yaml --status

# GUI
python gui/omv-gui.py

# Web (dev)
pip install web/requirements-web.txt
cd web && python app.py

# Web (deploy from dev machine)
cd web && python deploy-web.py
```

## Dependencies

- `cli/requirements.txt`: `paramiko>=3.0.0`, `pyyaml>=6.0`, `zeroconf>=0.149.0`
- `web/requirements-web.txt`: `flask`, `gunicorn`

## GUI features (gui/omv-gui.py)

### Connection
- **Conectar** dialog: Host/IP, Port (22), Usuário (root), Senha
- **Salvar senha** checkbox → saved to `omv-creds.json`
- **Buscar** button → mDNS discovery via `zeroconf` (`_ssh._tcp`, `_workstation._tcp`)

### Tabs
1. **Usuários** — lists system users (UID ≥ 1000, login shell). Add/Edit/Delete with SSH.
   - Groups multi-select; first = primary, rest = supplementary.
   - Edit shows password status (`passwd -S`).
2. **Pastas** — reads OMV shared folders from `config.xml`. Add/Edit/Delete.
   - Add: selects disk (from OMV fstab), group, permissions (presets), optional SMB share.
3. **Grupos** — lists groups from OMV XML. Add/Edit/Delete. Members multi-select.

### UUID generation
- Must use `str(uuid.uuid4())` (with dashes) — OMV validates UUIDv4 format.

## CLI flow (cli/omv-manager.py fixed order)

1. Create Linux groups
2. Register in OMV XML
3. Create Linux users + Samba password
4. Register in OMV XML
5. Create folders with `setgid`
6. Set ACLs
7. Register shared folders in OMV XML
8. Register SMB shares in OMV XML
9. `omv-salt deploy run samba`

## Web app (web/app.py)

### Routes
- `/` — Dashboard
- `/login`, `/logout` — Auth
- `/usuarios` — User CRUD
- `/grupos` — Group CRUD
- `/pastas` — Folder CRUD
- `/acls` — ACL management
- `/smb` — SMB share management
- `/exportar`, `/importar` — JSON backup

### Architecture
- `_login_required` renders `login.html` directly (200) instead of 302 redirect
- `_htx()` helper — HTMX partial or full page (wraps in `base.html`)
- `_ok()` / `_err()` — HTMX toast responses with `HX-Trigger: refreshList`
- Permissions use presets (`_PERM_PRESETS`) with radio buttons
- Light/dark theme toggle via `data-bs-theme` + `localStorage`

## Architecture notes

- **`gui/omv-gui.py`** imports `cli/omv-manager.py` via `importlib.util.spec_from_file_location`
- **`web/deploy-web.py`** imports `cli/omv-manager.py` for SSH and reads root `config.yaml`
- **`web/app.py`** imports `web/localshell.py` (same interface as SSHClient, uses `subprocess`)
- OMV state stored in `/etc/openmediavault/config.xml` — edited via `xml.etree.ElementTree`
- Base path (`/srv/dev-disk-by-uuid-...`) auto-detected from OMV config on connect
- Permissions: `stat -c '%A'` (e.g. `drwxrwsrwx`) auto-converted to octal (`775`) via `_sym_to_octal()`
- Portuguese UI labels throughout; no tests/linting/CI configured
