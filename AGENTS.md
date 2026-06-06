# OMV Manager — AGENTS.md

## Project

Automated management of users, groups, folders, ACLs, and SMB shares on OpenMediaVault 8.x (Debian 13) via SSH. Two implementations coexist:

- **`omv-manager.py`** — CLI tool, remote via `paramiko` SSH
- **`omv-manager.sh`** — standalone Bash alternative, runs directly on the OMV server

Both are **idempotent** and implement the same flow.

## Key files

| File | Role |
|---|---|
| `omv-manager.py` | CLI + remote SSH orchestration |
| `omv-manager.sh` | Bash in-situ alternative (run on the OMV server) |
| `omv-gui.py` | Tkinter GUI, imports `omv-manager.py` via `importlib` (hyphen in filename → `spec_from_file_location`) |
| `config.yaml` | YAML config for CLI tool (server, users, groups, folders, ACLs, SMB shares) |
| `omv-gui.py` + `omv-creds.json` | GUI saves connection credentials when "Salvar senha" is checked |

## Commands

```bash
pip install -r requirements.txt

python omv-manager.py --config config.yaml --apply
python omv-manager.py --config config.yaml --dry-run
python omv-manager.py --config config.yaml --status
python omv-gui.py

# Bash variant (run on OMV server):
./omv-manager.sh                  # apply all
./omv-manager.sh --dry-run
./omv-manager.sh --status
```

## Dependencies

- `paramiko>=3.0.0`, `pyyaml>=6.0`, `zeroconf>=0.149.0`
- `zeroconf` is used by the GUI's "Buscar" (mDNS discovery) feature

## GUI features (omv-gui.py)

### Connection
- **Conectar** dialog: Host/IP, Port (22), Usuário (root), Senha
- **Salvar senha** checkbox → saved to `omv-creds.json`
- **Buscar** button → mDNS discovery via `zeroconf` (`_ssh._tcp`, `_workstation._tcp`), shows results in a TreeView dialog

### Tabs
1. **Usuários** — lists system users (UID ≥ 1000, login shell). Add/Edit/Delete with SSH.
   - Groups are multi-select; first selected = primary group, rest = supplementary.
   - Edit mode shows password status (`passwd -S`).
2. **Pastas** — reads OMV shared folders from `config.xml`. Add/Edit/Delete.
   - **Add**: selects disk (from OMV fstab), group, permissions, optional SMB share.
   - Creates directory on the disk mount point, registers shared folder in XML, optionally creates SMB share.
   - Runs `omv-salt deploy run samba` + `systemctl restart smbd nmbd`.
3. **Grupos** — lists groups from OMV XML. Add/Edit/Delete.
   - Members are multi-select from user list.
   - Add: `groupadd` + XML registration via base64-safe Python script.

### UUID generation
- Must use `str(uuid.uuid4())` (with dashes) — OMV validates UUIDv4 format.
- `.hex[:36]` (no dashes) causes `SchemaValidationException`.

### Architecture
- All dialogs use `self.root.wait_window(dlg)` (not `self.wait_window(dlg)`).
- SSH operations run in `threading.Thread`; results pushed via `queue.Queue`.
- Dark theme (`BG="#1e1e2e"`, `FG="#cdd6f4"`, `ACCENT="#89b4fa"`, etc.).
- ttk.Notebook for tabs with custom dark style.
- ttk.Treeview for scan results dialog.
- Menu bar: Ajuda → Instruções / Sobre.

## CLI operations (omv-manager.py fixed order)

1. Create Linux groups
2. Register groups in OMV XML (`/etc/openmediavault/config.xml`)
3. Create Linux users (with Samba password via `smbpasswd`)
4. Register users in OMV XML
5. Create folders on disk with `setgid`
6. Set ACLs (user/group, with default ACLs)
7. Register shared folders in OMV XML (with privilege ACLs)
8. Register SMB shares in OMV XML
9. Apply with `omv-salt deploy run samba` (skipped in dry-run)

## Architecture notes

- Both `omv-manager.py` and `omv-manager.sh` are self-contained — changes must be mirrored.
- `omv-gui.py` loads `omv-manager.py` via `importlib.util.spec_from_file_location`.
- OMV state stored in `/etc/openmediavault/config.xml` (XML). Edited directly via `xml.etree.ElementTree` or `xmlstarlet`.
- `omv-manager.sh` requires `jq` and `xmlstarlet` on the server.
- `config.yaml` is the single source of truth for the CLI tool.
- Base path (`/srv/dev-disk-by-uuid-...`) is auto-detected from OMV config on connect.
- Portuguese UI labels throughout; no tests/linting/CI configured.
