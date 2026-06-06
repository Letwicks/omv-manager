# OMV Manager

Gestão automatizada de **usuários, grupos, pastas, ACLs e compartilhamentos SMB** no **OpenMediaVault 8.x** (Debian 13).

Três implementações coexistem neste repositório — escolha a mais adequada:

| App | Tipo | Onde roda | Interface |
|---|---|---|---|
| `omv-manager.py` | CLI | Qualquer máquina (SSH remoto) | Terminal |
| `omv-gui.py` | GUI (Tkinter) | Windows/Linux desktop (SSH remoto) | Janela gráfica |
| `app.py` | Web (Flask) | **Direto no servidor OMV** | Navegador |

---

## 1. omv-manager.py (CLI)

Aplicação **original** de linha de comando. Conecta via SSH ao servidor OMV e executa todo o fluxo de criação/remoção lendo um arquivo `config.yaml`.

### Dependências

```bash
pip install -r requirements.txt
```

### Uso

```bash
python omv-manager.py --config config.yaml --apply    # aplicar config
python omv-manager.py --config config.yaml --dry-run   # simular
python omv-manager.py --config config.yaml --status    # status do servidor
```

### Fluxo (ordem fixa)

1. Criar grupos Linux (`groupadd`)
2. Registar grupos no XML do OMV
3. Criar usuários Linux (`useradd`) + senha Samba (`smbpasswd`)
4. Registar usuários no XML do OMV
5. Criar pastas no disco com `setgid`
6. Definir ACLs (usuário/grupo, com default ACLs)
7. Registar shared folders no XML do OMV
8. Registar SMB shares no XML do OMV
9. Aplicar com `omv-salt deploy run samba`

---

## 2. omv-gui.py (GUI — Tkinter)

Interface gráfica **desktop** que conecta via SSH ao servidor OMV. Permite gestão visual de todos os recursos.

### Dependências

```bash
pip install -r requirements.txt
```

### Uso

```bash
python omv-gui.py
```

### Funcionalidades

- **Conexão**: Host/IP, Porta (22), Usuário (root), Senha
- **Buscar**: Descoberta mDNS via `zeroconf` (`_ssh._tcp`, `_workstation._tcp`)
- **Salvar senha**: Opcional, guarda em `omv-creds.json`
- **Abas**:
  - **Usuários** — CRUD completo + senha Samba `passwd -S`
  - **Grupos** — CRUD com seleção multi-membros
  - **Pastas** — CRUD com seleção de disco, grupo, permissão (presets), SMB opcional
- **Permissões**: Radio buttons com presets em português (770, 755, 750, 777, 700) + campo personalizado
- **Importar/Exportar**: Backup completo em JSON

---

## 3. app.py (Web — Flask)

Interface **web** que roda **diretamente no servidor OMV** (sem SSH). Usa `subprocess` local.

### Dependências (lado servidor)

```bash
pip install flask gunicorn
```

### Uso (desenvolvimento)

```bash
export OMV_WEB_PASS=omvadmin
python app.py
```

### Uso (produção — systemd)

```bash
# Via script de deploy (a partir da máquina Windows):
python deploy-web.py

# Ou manualmente no servidor:
cp deploy/omv-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now omv-web
# Acessar: http://<servidor>:8080
```

### Funcionalidades

- **Dashboard** — visão geral: recursos, CPU, memória, disco, uptime
- **Usuários** — CRUD (com senha + Samba)
- **Grupos** — CRUD com membros multi-select
- **Pastas** — CRUD com permissão por presets
- **ACLs** — gerir permissões de pasta (user/group, rwx/rx/r)
- **SMB** — gerir compartilhamentos, ativar/desativar
- **Exportar/Importar** — backup JSON completo
- **Tema** — alternar claro/escuro (salvo no navegador)
- **Stack**: Flask + HTMX + Bootstrap 5.3 (dark)

### Login

Senha definida pela variável de ambiente `OMV_WEB_PASS` (padrão: `omvadmin`).

---

## Arquitetura

```
/
├── omv-manager.py          # CLI — orquestração via SSH (paramiko)
├── omv-manager.sh          # Bash alternativo (roda direto no servidor)
├── omv-gui.py              # GUI Tkinter (desktop, SSH remoto)
├── app.py                  # Web Flask (roda no servidor, subprocess)
├── localshell.py           # Substituto de SSHClient via subprocess
├── deploy-web.py           # Deploy automático do web app via SSH
├── config.yaml             # Config (gitignorado — dados sensíveis)
├── config.yaml.example     # Template de config sem dados reais
├── requirements.txt        # Deps: paramiko, pyyaml, zeroconf
├── requirements-web.txt    # Deps do web app: flask, gunicorn
├── deploy/
│   ├── omv-web.service     # Systemd unit (porta 8080)
│   └── omv-manager.conf    # Nginx location (opcional)
└── templates/              # Jinja2 templates do web app
    ├── base.html
    ├── login.html
    ├── dashboard.html
    ├── usuarios.html
    ├── usuario_form.html
    ├── grupos.html
    ├── grupo_form.html
    ├── pastas.html
    ├── pasta_form.html
    ├── acls.html
    ├── acl_form.html
    ├── smb.html
    └── smb_form.html
```

## Notas técnicas

- **UUID**: Sempre `str(uuid.uuid4())` (com traços) — OMV valida UUIDv4 com traços
- **omv-gui.py** importa `omv-manager.py` via `importlib.util.spec_from_file_location` (devido ao hífen no nome)
- **app.py** importa `localshell.py` (mesma interface de `SSHClient`, mas usa `subprocess`)
- **Todas as permissões** nas interfaces web e GUI usam **presets** em português com conversão automática de formato simbólico (`drwxrwsrwx`) para octal (`775`)
- **Idempotente**: todas as operações podem ser repetidas sem duplicar entradas
