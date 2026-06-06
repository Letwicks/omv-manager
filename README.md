# OMV Manager

Gestão automatizada de **usuários, grupos, pastas, ACLs e compartilhamentos SMB** no **OpenMediaVault 8.x** (Debian 13).

Três implementações coexistem neste repositório — cada uma na sua pasta:

| App | Pasta | Tipo | Onde roda | Interface |
|---|---|---|---|---|
| CLI | `cli/` | Terminal | Qualquer máquina (SSH remoto) | Linha de comando |
| GUI | `gui/` | Tkinter | Windows/Linux desktop (SSH remoto) | Janela gráfica |
| Web | `web/` | Flask | **Direto no servidor OMV** | Navegador |

```
/
├── cli/          # Aplicação de linha de comando
├── gui/          # Interface gráfica desktop
├── web/          # Interface web (Flask)
├── config.yaml   # Configuração (gitignorado — dados sensíveis)
├── README.md
└── .gitignore
```

---

## CLI — `cli/omv-manager.py`

Aplicação **original** de linha de comando. Conecta via SSH ao servidor OMV e executa todo o fluxo de criação/remoção lendo um arquivo `config.yaml`.

### Dependências

```bash
pip install cli/requirements.txt
```

### Uso

```bash
python cli/omv-manager.py --config config.yaml --apply    # aplicar config
python cli/omv-manager.py --config config.yaml --dry-run   # simular
python cli/omv-manager.py --config config.yaml --status    # status do servidor
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

## GUI — `gui/omv-gui.py`

Interface gráfica **desktop** que conecta via SSH ao servidor OMV.

### Dependências

```bash
pip install cli/requirements.txt    # compartilha as mesmas deps do CLI
```

### Uso

```bash
python gui/omv-gui.py
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

## Web — `web/app.py`

Interface **web** que roda **diretamente no servidor OMV** (sem SSH). Usa `subprocess` local.

### Dependências (lado servidor)

```bash
pip install -r web/requirements-web.txt
```

### Uso (desenvolvimento)

```bash
export OMV_WEB_PASS=omvadmin
cd web && python app.py
```

### Uso (produção — systemd)

```bash
cd web && python deploy-web.py
```

Ou manualmente no servidor:

```bash
cp web/deploy/omv-web.service /etc/systemd/system/
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

## Estrutura completa

```
/
├── cli/
│   ├── omv-manager.py          # CLI — orquestração via SSH (paramiko)
│   ├── requirements.txt        # Deps: paramiko, pyyaml, zeroconf
│   └── config.yaml.example     # Template de config sem dados reais
│
├── gui/
│   └── omv-gui.py              # GUI Tkinter (desktop, SSH remoto)
│
├── web/
│   ├── app.py                  # Web Flask (roda no servidor, subprocess)
│   ├── localshell.py           # Substituto de SSHClient via subprocess
│   ├── deploy-web.py           # Deploy automático do web app via SSH
│   ├── requirements-web.txt    # Deps do web app: flask, gunicorn
│   ├── deploy/
│   │   ├── omv-web.service     # Systemd unit (porta 8080)
│   │   └── omv-manager.conf    # Nginx location (opcional)
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── dashboard.html
│       ├── usuarios.html
│       ├── usuario_form.html
│       ├── grupos.html
│       ├── grupo_form.html
│       ├── pastas.html
│       ├── pasta_form.html
│       ├── acls.html
│       ├── acl_form.html
│       ├── smb.html
│       └── smb_form.html
│
├── config.yaml                 # Config (gitignorado — dados sensíveis)
├── README.md
└── .gitignore
```

## Notas técnicas

- **UUID**: Sempre `str(uuid.uuid4())` (com traços) — OMV valida UUIDv4 com traços
- **gui/omv-gui.py** importa `cli/omv-manager.py` via `importlib.util.spec_from_file_location` (devido ao hífen no nome)
- **web/app.py** importa `web/localshell.py` (mesma interface de `SSHClient`, mas usa `subprocess`)
- **web/deploy-web.py** lê `config.yaml` da raiz do projeto e importa `cli/omv-manager.py`
- **Todas as permissões** nas interfaces web e GUI usam **presets** em português com conversão automática de formato simbólico (`drwxrwsrwx`) para octal (`775`)
- **Idempotente**: todas as operações podem ser repetidas sem duplicar entradas
