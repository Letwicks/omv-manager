#!/usr/bin/env python3
"""
Deploy OMV Manager Web no servidor via SSH (config.yaml).
Copia arquivos, instala deps, configura systemd + nginx.
"""

import os
import sys
import io
import tarfile
import time
import importlib.util

import yaml
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_OMV_MOD = os.path.join(ROOT, "cli", "omv-manager.py")
_spec = importlib.util.spec_from_file_location("omv_manager_mod", _OMV_MOD)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ServerConfig = _mod.ServerConfig
SSHClient = _mod.SSHClient
CFG_PATH = os.path.join(ROOT, "config.yaml")

REMOTE_DIR = "/opt/omv-manager-web"

FILES = [
    "app.py",
    "localshell.py",
    "requirements-web.txt",
    "templates/base.html",
    "templates/login.html",
    "templates/dashboard.html",
    "templates/usuarios.html",
    "templates/usuario_form.html",
    "templates/grupos.html",
    "templates/grupo_form.html",
    "templates/pastas.html",
    "templates/pasta_form.html",
    "templates/acls.html",
    "templates/acl_form.html",
    "templates/smb.html",
    "templates/smb_form.html",
    "deploy/omv-web.service",
    "deploy/omv-manager.conf",
]

NGINX_D = "/etc/nginx/openmediavault-webui.d"


def _b(msg):
    print(f"\n=== {msg} ===")


def main():
    if not os.path.exists(CFG_PATH):
        print(f"Erro: {CFG_PATH} não encontrado")
        sys.exit(1)

    with open(CFG_PATH) as f:
        cfg = yaml.safe_load(f)

    srv = cfg["server"]
    host, port, user, password = srv["host"], srv["port"], srv["username"], srv["password"]

    _b(f"Conectando a {host}:{port} como {user}")
    sc = ServerConfig(host=host, port=port, username=user, password=password)
    ssh = SSHClient(sc)
    ssh.connect()
    print("Conectado.")

    # --- Criar diretórios remotos ---
    _b("Criando diretórios")
    ssh.exec(f"mkdir -p {REMOTE_DIR}/templates {REMOTE_DIR}/deploy")
    ssh.exec(f"mkdir -p {NGINX_D}")
    print("Diretórios criados.")

    # --- Upload dos arquivos via tar em memória ---
    _b("Copiando arquivos")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in FILES:
            local = os.path.join(HERE, rel)
            if os.path.exists(local):
                tar.add(local, arcname=rel)
                print(f"  + {rel}")
    buf.seek(0)
    data = buf.read()
    b64 = __import__("base64").b64encode(data).decode()

    script = (
        "import base64, tarfile, io\n"
        f"d = base64.b64decode('{b64}')\n"
        "buf = io.BytesIO(d)\n"
        f"with tarfile.open(fileobj=buf) as t:\n"
        f"    t.extractall(path='{REMOTE_DIR}')\n"
    )
    ssh.exec_python(script)
    print("Arquivos extraídos.")

    # --- Instalar dependências ---
    _b("Instalando dependências Python")
    code, out, err = ssh.exec(
        f"pip3 install -r {REMOTE_DIR}/requirements-web.txt 2>&1",
        timeout=120,
    )
    safe = (out or err).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    print(safe[:600])

    # --- Configurar senha ---
    _b("Configurando senha de acesso")
    import uuid
    default_pass = "omvadmin"
    secret = uuid.uuid4().hex
    sed_pass = (
        f"sed -i 's/OMV_WEB_PASS=omvadmin/OMV_WEB_PASS={default_pass}/' "
        f"{REMOTE_DIR}/deploy/omv-web.service"
    )
    sed_sec = (
        f"sed -i 's/change_this_random_secret/{secret}/' "
        f"{REMOTE_DIR}/deploy/omv-web.service"
    )
    ssh.exec(sed_pass)
    ssh.exec(sed_sec)
    print(f"Senha: {default_pass}")

    # --- Systemd ---
    _b("Configurando systemd")
    ssh.exec(f"cp {REMOTE_DIR}/deploy/omv-web.service /etc/systemd/system/")
    ssh.exec("systemctl daemon-reload")
    ssh.exec("systemctl enable omv-web")
    ssh.exec("systemctl restart omv-web")
    print("omv-web.service iniciado.")

    # --- Nginx (opcional, pode conflitar com OMV Web UI) ---
    _b("Configurando Nginx (opcional)")
    code, out, err = ssh.exec(f"cp {REMOTE_DIR}/deploy/omv-manager.conf {NGINX_D}/", timeout=10)
    code2, out2, err2 = ssh.exec("nginx -t 2>&1", timeout=10)
    if code2 == 0:
        ssh.exec("systemctl reload nginx")
        print("Nginx recarregado.")
    else:
        print(f"Nginx config ignorado ({err2[:200]}).")
        print("Acesse via porta direta.")

    # --- Info final ---
    _b("Deploy concluído!")
    ssh.close()
    print(f"\nAcesse: http://{host}:8080")
    print(f"Senha:  {default_pass}")
    print(f"\nPara alterar a senha:")
    print(f"  ssh root@{host}")
    print(f"  export OMV_WEB_PASS=nova_senha")
    print(f"  systemctl restart omv-web")


if __name__ == "__main__":
    main()
