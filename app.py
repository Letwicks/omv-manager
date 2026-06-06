#!/usr/bin/env python3
"""
OMV Manager Web — roda diretamente no servidor OMV.
Requer: flask, gunicorn
"""

import os
import re
import json
import uuid
import base64
import subprocess
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, send_file, make_response,
)

from localshell import LocalShell

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OMV_XML = Path("/etc/openmediavault/config.xml")
OMV_SALT = "omv-salt deploy run samba 2>&1"

app = Flask(__name__)
app.secret_key = os.environ.get("OMV_WEB_SECRET", uuid.uuid4().hex)


@app.context_processor
def _inject_globals():
    return {"presets": _PERM_PRESETS}


_PASS = os.environ.get("OMV_WEB_PASS", "omvadmin")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

sh = LocalShell()


def _get_xml():
    return ET.parse(str(OMV_XML)).getroot()


def _save_xml(root):
    ET.register_namespace("", "")
    ET.indent(root)
    root.write(str(OMV_XML), encoding="UTF-8", xml_declaration=True)


def _uuid():
    return str(uuid.uuid4())


def _sym_to_octal(sym):
    """Converte 'drwxrwsrwx' → '775' (ignora tipo e bits especiais)."""
    if not sym or len(sym) < 10:
        return "770"
    p = sym[1:10].replace("s", "x").replace("S", "-").replace("t", "x").replace("T", "-")
    val = 0
    for i, ch in enumerate(p):
        if ch != "-":
            val |= 1 << (8 - i)
    return oct(val)[2:].zfill(3)


_PERM_PRESETS = {
    "770": "Dono e grupo: acesso total / Outros: nenhum",
    "755": "Dono: acesso total / Grupo+Outros: ler e executar",
    "750": "Dono: acesso total / Grupo: ler e executar / Outros: nenhum",
    "777": "Todos: acesso total",
    "700": "Apenas dono: acesso total",
}


def _login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("logged_in"):
            return render_template("login.html")
        return f(*a, **kw)
    return wrapper


def _htx(template, **kw):
    """Shortcut for HTMX partial vs full page."""
    if request.headers.get("HX-Request"):
        return render_template(template, **kw)
    return render_template("base.html", content=template, **kw)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("senha") == _PASS:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Senha incorreta", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.route("/")
@_login_required
def dashboard():
    info = {}
    try:
        _, hostname, _ = sh.exec("hostname")
        info["hostname"] = hostname.strip()

        _, os_info, _ = sh.exec(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
        )
        info["os"] = os_info.strip() or "Desconhecido"

        _, uptime, _ = sh.exec("uptime -p 2>/dev/null || uptime")
        info["uptime"] = uptime.strip()

        _, mem, _ = sh.exec(
            "free -h | awk '/^Mem:/ {print $3\"/\"$2}'",
        )
        info["mem"] = mem.strip()

        _, disk, _ = sh.exec(
            "df -h / | awk 'NR==2{print $2\" total, \"$4\" livre, \"$5\" usado\"}'",
        )
        info["disk"] = disk.strip()

        _, load, _ = sh.exec("cat /proc/loadavg | awk '{print $1\", \"$2\", \"$3}'")
        info["load"] = load.strip()

        # OMV version
        _, omv_v, _ = sh.exec(
            "dpkg -l openmediavault 2>/dev/null | awk '/^ii/{print $3}'",
        )
        info["omv"] = omv_v.strip() or "?"

        info["users"] = _load_users_count()
        info["groups"] = _load_omv_groups_count()
        info["folders"] = _load_folders_count()
    except Exception as e:
        flash(f"Erro ao carregar dados: {e}", "danger")

    return _htx("dashboard.html", info=info)


# ---------------------------------------------------------------------------
# Shared loaders
# ---------------------------------------------------------------------------


def _load_omv_groups():
    """Lista de {name, comment, members}."""
    groups = []
    try:
        root = _get_xml()
        for g in root.findall(".//system/usermanagement/groups/group"):
            name = (g.findtext("name") or "").strip()
            if not name:
                continue
            comment = (g.findtext("comment") or "").strip()
            _, m_out, _ = sh.exec(
                f"getent group '{name}' 2>/dev/null | cut -d: -f4",
            )
            members = [x.strip() for x in m_out.strip().split(",") if x.strip()]
            groups.append({"name": name, "comment": comment, "members": members})
    except Exception:
        pass
    return groups


def _load_users():
    """Lista de {username, uid, primary_group, groups, gecos}."""
    users = []
    try:
        _, out, _ = sh.exec(
            "getent passwd | awk -F: '$3>=1000 && $3<65534 "
            "&& $7 !~ /\\/(nologin|false)$/ {print}'",
        )
        for line in out.strip().split("\n"):
            if not line:
                continue
            parts = line.split(":")
            username = parts[0]
            _, g_out, _ = sh.exec(f"groups '{username}' 2>/dev/null")
            all_groups = (
                g_out.replace(f"{username} : ", "").strip().split()
                if g_out else []
            )
            _, pg_out, _ = sh.exec(f"getent group {parts[3]} | cut -d: -f1")
            primary = pg_out.strip()
            extra = [g for g in all_groups if g != primary]
            users.append({
                "username": username,
                "uid": parts[2],
                "primary_group": primary,
                "gecos": parts[4],
                "groups": ",".join(extra),
            })
    except Exception:
        pass
    return users


def _load_folders():
    """Lista de {name, path, disk, group, permissions, comment}."""
    folders = []
    try:
        root = _get_xml()
        mount_map = {}
        for m in root.findall(".//mntent"):
            muuid = (m.findtext("uuid") or "").strip()
            mdir = (m.findtext("dir") or "").strip()
            if muuid and mdir:
                mount_map[muuid] = mdir
        for sf in root.findall(".//sharedfolder"):
            name = (sf.findtext("name") or "").strip()
            mref = (sf.findtext("mntentref") or "").strip()
            rel = (sf.findtext("reldirpath") or "").strip().rstrip("/")
            mount = mount_map.get(mref, "")
            path = f"{mount}/{rel}" if mount else rel
            _, stat_out, _ = sh.exec(
                f"stat -c '%U:%G:%A' '{path}' 2>/dev/null",
            )
            sp = stat_out.strip().split(":")
            folders.append({
                "name": name,
                "path": path,
                "disk": mount,
                "group": sp[1] if len(sp) > 1 else "",
                "permissions": _sym_to_octal(sp[2]) if len(sp) > 2 else "770",
                "comment": (sf.findtext("comment") or "").strip(),
            })
    except Exception:
        pass
    return folders


def _load_disks():
    """Lista de {uuid, dev, mount, fstype, size, avail}."""
    disks = []
    try:
        root = _get_xml()
        for m in root.findall(".//mntent"):
            uuid_ = (m.findtext("uuid") or "").strip()
            fsname = (m.findtext("fsname") or "").strip()
            mount = (m.findtext("dir") or "").strip()
            fstype = (m.findtext("type") or "").strip()
            if not uuid_ or not mount:
                continue
            _, real_out, _ = sh.exec(
                f"findmnt -n -o SOURCE -T '{mount}' 2>/dev/null",
            )
            real = real_out.strip()
            if real and real != "/dev/root":
                fsname = real
            dev = fsname.rsplit("/", 1)[-1] if fsname else "?"
            _, df_out, _ = sh.exec(f"df -h '{mount}' 2>/dev/null | tail -1")
            parts = df_out.strip().split()
            size = parts[1] if len(parts) > 1 else "?"
            avail = parts[3] if len(parts) > 3 else "?"
            disks.append({
                "uuid": uuid_,
                "dev": dev,
                "mount": mount,
                "fstype": fstype,
                "size": size,
                "avail": avail,
            })
    except Exception:
        pass
    return disks


def _load_omv_groups_count():
    try:
        root = _get_xml()
        return len(root.findall(".//system/usermanagement/groups/group"))
    except Exception:
        return 0


def _load_folders_count():
    try:
        root = _get_xml()
        return len(root.findall(".//sharedfolder"))
    except Exception:
        return 0


def _load_users_count():
    _, out, _ = sh.exec(
        "getent passwd | awk -F: '$3>=1000 && $3<65534 "
        "&& $7 !~ /\\/(nologin|false)$/ {print}' | wc -l",
    )
    try:
        return int(out.strip())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# USUÁRIOS
# ---------------------------------------------------------------------------


@app.route("/usuarios")
@_login_required
def usuarios():
    users = _load_users()
    groups = [g["name"] for g in _load_omv_groups()]
    return _htx("usuarios.html", users=users, groups=groups, all_groups=groups)


@app.route("/usuarios/novo", methods=["GET"])
@_login_required
def usuario_novo_form():
    groups = [g["name"] for g in _load_omv_groups()]
    return render_template("usuario_form.html", user=None, groups=groups,
                           all_groups=groups)


@app.route("/usuarios/novo", methods=["POST"])
@_login_required
def usuario_novo():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    primary = request.form.get("primary_group", "").strip()
    extra = request.form.get("extra_groups", "").strip()
    email = request.form.get("email", "").strip()

    if not username or not primary:
        return _err("Usuário e grupo primário obrigatórios")

    code, out, err = sh.exec(f"id '{username}' >/dev/null 2>&1 || echo NO")
    if "NO" not in out:
        return _err(f"Usuário '{username}' já existe")

    sh.exec_assert(f"useradd -m -g '{primary}' -s /bin/bash '{username}'")
    if password:
        sh.exec_assert(f"echo '{username}:{password}' | chpasswd")
    for eg in [g.strip() for g in extra.split(",") if g.strip()]:
        sh.exec_assert(f"usermod -aG '{eg}' '{username}'")
    if password:
        sh.exec_assert(
            f"(echo '{password}'; echo '{password}') | smbpasswd -a -s '{username}'",
        )
    return _ok("Usuário criado")


@app.route("/usuarios/<username>/editar", methods=["GET"])
@_login_required
def usuario_editar_form(username):
    users = _load_users()
    user = next((u for u in users if u["username"] == username), None)
    if not user:
        return _err("Usuário não encontrado", 404)
    # password status
    _, ps_out, _ = sh.exec(f"passwd -S '{username}' 2>/dev/null")
    user["pass_status"] = ps_out.strip()
    groups = [g["name"] for g in _load_omv_groups()]
    return render_template("usuario_form.html", user=user, groups=groups,
                           all_groups=groups)


@app.route("/usuarios/<username>/editar", methods=["POST"])
@_login_required
def usuario_editar(username):
    password = request.form.get("password", "").strip()
    primary = request.form.get("primary_group", "").strip()
    extra = request.form.get("extra_groups", "").strip()
    email = request.form.get("email", "").strip()

    if primary:
        sh.exec_assert(f"usermod -g '{primary}' '{username}'")
    if extra:
        sh.exec_assert(f"usermod -G '{extra}' '{username}'")
    if password:
        sh.exec_assert(f"echo '{username}:{password}' | chpasswd")
        sh.exec_assert(
            f"(echo '{password}'; echo '{password}') | smbpasswd -s '{username}'",
        )
    return _ok("Usuário atualizado")


@app.route("/usuarios/<username>/apagar", methods=["POST"])
@_login_required
def usuario_apagar(username):
    try:
        sh.exec_assert(f"pdbedit -x '{username}' 2>/dev/null", timeout=10)
        sh.exec_assert(
            f"userdel -r '{username}' 2>/dev/null || userdel '{username}'",
            timeout=15,
        )
        return _ok("Usuário removido")
    except Exception as e:
        return _err(f"Erro ao remover usuário: {e}")


# ---------------------------------------------------------------------------
# GRUPOS
# ---------------------------------------------------------------------------


@app.route("/grupos")
@_login_required
def grupos():
    groups = _load_omv_groups()
    users = [u["username"] for u in _load_users()]
    return _htx("grupos.html", groups=groups, users=users)


@app.route("/grupos/novo", methods=["GET"])
@_login_required
def grupo_novo_form():
    users = [u["username"] for u in _load_users()]
    return render_template("grupo_form.html", group=None, users=users)


@app.route("/grupos/novo", methods=["POST"])
@_login_required
def grupo_novo():
    name = request.form.get("name", "").strip()
    comment = request.form.get("comment", "").strip()
    members = request.form.getlist("members")

    if not name:
        return _err("Nome do grupo obrigatório")

    if sh.group_exists(name):
        return _err(f"Grupo '{name}' já existe")

    sh.exec_assert(f"groupadd '{name}'")
    for m in members:
        sh.exec_assert(f"usermod -aG '{name}' '{m}'")

    _register_omv_group(name, comment)
    return _ok("Grupo criado")


@app.route("/grupos/<name>/editar", methods=["GET"])
@_login_required
def grupo_editar_form(name):
    groups = _load_omv_groups()
    group = next((g for g in groups if g["name"] == name), None)
    if not group:
        return _err("Grupo não encontrado", 404)
    users = [u["username"] for u in _load_users()]
    return render_template("grupo_form.html", group=group, users=users)


@app.route("/grupos/<name>/editar", methods=["POST"])
@_login_required
def grupo_editar(name):
    comment = request.form.get("comment", "").strip()
    new_members = request.form.getlist("members")

    old = sh.exec_assert(f"getent group '{name}' 2>/dev/null | cut -d: -f4")
    old_members = [x.strip() for x in old.split(",") if x.strip()]

    added = [m for m in new_members if m not in old_members]
    removed = [m for m in old_members if m not in new_members]

    for m in removed:
        sh.exec_assert(
            f"gpasswd -d '{m}' '{name}' 2>/dev/null || "
            f"deluser '{m}' '{name}' 2>/dev/null || true",
            timeout=10,
        )
    for m in added:
        sh.exec_assert(f"usermod -aG '{name}' '{m}'")

    _update_omv_group_comment(name, comment)
    return _ok("Grupo atualizado")


@app.route("/grupos/<name>/apagar", methods=["POST"])
@_login_required
def grupo_apagar(name):
    _remove_omv_group(name)
    sh.exec_assert(f"groupdel '{name}' 2>/dev/null || true", timeout=10)
    return _ok("Grupo removido")


def _register_omv_group(name, comment):
    nb = base64.b64encode(name.encode()).decode()
    cb = base64.b64encode(comment.encode()).decode()
    script = (
        "import xml.etree.ElementTree as ET, base64\n"
        "ET.register_namespace('', '')\n"
        f"n=base64.b64decode('{nb}').decode()\n"
        f"c=base64.b64decode('{cb}').decode()\n"
        "t=ET.parse('/etc/openmediavault/config.xml')\n"
        "r=t.getroot()\n"
        "p=r.find('.//system/usermanagement/groups')\n"
        "if p is None:\n"
        "    raise Exception('groups node not found')\n"
        "g=ET.SubElement(p,'group')\n"
        f"ET.SubElement(g,'uuid').text='{_uuid()}'\n"
        "ET.SubElement(g,'name').text=n\n"
        "ET.SubElement(g,'comment').text=c\n"
        "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)\n"
    )
    sh.exec_python(script)


def _update_omv_group_comment(name, comment):
    nb = base64.b64encode(name.encode()).decode()
    cb = base64.b64encode(comment.encode()).decode()
    script = (
        "import xml.etree.ElementTree as ET, base64\n"
        "ET.register_namespace('', '')\n"
        f"n=base64.b64decode('{nb}').decode()\n"
        f"c=base64.b64decode('{cb}').decode()\n"
        "t=ET.parse('/etc/openmediavault/config.xml')\n"
        "r=t.getroot()\n"
        "g=r.find(\".//system/usermanagement/groups/group[name='\"+n+\"']\")\n"
        "if g is not None:\n"
        "    ct=g.find('comment')\n"
        "    if ct is not None:\n"
        "        ct.text=c\n"
        "    else:\n"
        "        ET.SubElement(g,'comment').text=c\n"
        "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)\n"
    )
    sh.exec_python(script)


def _remove_omv_group(name):
    nb = base64.b64encode(name.encode()).decode()
    script = (
        "import xml.etree.ElementTree as ET, base64\n"
        "ET.register_namespace('', '')\n"
        f"n=base64.b64decode('{nb}').decode()\n"
        "t=ET.parse('/etc/openmediavault/config.xml')\n"
        "r=t.getroot()\n"
        "g=r.find(\".//system/usermanagement/groups/group[name='\"+n+\"']\")\n"
        "if g is not None:\n"
        "    p=r.find('.//system/usermanagement/groups')\n"
        "    if p is not None:\n"
        "        p.remove(g)\n"
        "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)\n"
    )
    sh.exec_python(script)


# ---------------------------------------------------------------------------
# PASTAS
# ---------------------------------------------------------------------------


@app.route("/pastas")
@_login_required
def pastas():
    folders = _load_folders()
    groups = [g["name"] for g in _load_omv_groups()]
    disks = _load_disks()
    return _htx("pastas.html", folders=folders, groups=groups, disks=disks)


@app.route("/pastas/novo", methods=["GET"])
@_login_required
def pasta_novo_form():
    groups = [g["name"] for g in _load_omv_groups()]
    disks = _load_disks()
    return render_template("pasta_form.html", folder=None, groups=groups,
                           disks=disks, all_groups=groups)


@app.route("/pastas/novo", methods=["POST"])
@_login_required
def pasta_novo():
    name = request.form.get("name", "").strip()
    group = request.form.get("group", "users").strip()
    perms = request.form.get("permissions", "").strip() or request.form.get("permissions_custom", "").strip() or "770"
    disk_uuid = request.form.get("disk_uuid", "").strip()
    create_smb = request.form.get("create_smb") == "on"

    if not name or not disk_uuid:
        return _err("Nome e disco obrigatórios")
    if not re.match(r"^\d{3,4}$", perms):
        return _err("Permissão deve ser 3-4 dígitos (ex: 770)")

    root = _get_xml()
    m = root.find(f".//mntent[uuid='{disk_uuid}']")
    if m is None:
        return _err("Disco não encontrado")
    disk_mount = (m.findtext("dir") or "").strip()
    if not disk_mount:
        return _err("Disco sem mount point")

    path = f"{disk_mount}/{name}"
    code, _, _ = sh.exec(f"test -d '{path}'")
    if code == 0:
        return _err(f"Pasta '{path}' já existe")

    sh.exec_assert(f"mkdir -p '{path}'")
    sh.exec_assert(f"chown root:'{group}' '{path}'")
    sh.exec_assert(f"chmod {perms} '{path}'")
    sh.exec_assert(f"chmod g+s '{path}'")

    sf_uuid = _uuid()
    smb_uuid = _uuid() if create_smb else ""

    lines = [
        "import xml.etree.ElementTree as ET",
        "ET.register_namespace('', '')",
        "t=ET.parse('/etc/openmediavault/config.xml')",
        "r=t.getroot()",
        f"sf_uuid='{sf_uuid}'",
        f"smb_uuid='{smb_uuid}'",
        "# sharedfolder",
        "p=r.find('.//system/shares')",
        "if p is None:",
        "    sn=r.find('.//system')",
        "    if sn is None: raise Exception('no system')",
        "    p=ET.SubElement(sn,'shares')",
        "nf=ET.SubElement(p,'sharedfolder')",
        "ET.SubElement(nf,'uuid').text=sf_uuid",
        f"ET.SubElement(nf,'name').text='{name}'",
        "ET.SubElement(nf,'comment').text=''",
        f"ET.SubElement(nf,'mntentref').text='{disk_uuid}'",
        f"ET.SubElement(nf,'reldirpath').text='{name}/'",
    ]
    if create_smb:
        lines += [
            "# SMB share",
            "smb=r.find('.//services/smb')",
            "if smb is None:",
            "    svc=r.find('.//services')",
            "    if svc is None: raise Exception('no services')",
            "    smb=ET.SubElement(svc,'smb')",
            "sh=smb.find('shares')",
            "if sh is None:",
            "    sh=ET.SubElement(smb,'shares')",
            "ns=ET.SubElement(sh,'share')",
            "ET.SubElement(ns,'uuid').text=smb_uuid",
            "ET.SubElement(ns,'enable').text='1'",
            "ET.SubElement(ns,'sharedfolderref').text=sf_uuid",
            f"ET.SubElement(ns,'comment').text='{name}'",
            "ET.SubElement(ns,'guest').text='no'",
            "ET.SubElement(ns,'readonly').text='0'",
            "ET.SubElement(ns,'browseable').text='true'",
            "ET.SubElement(ns,'recyclebin').text='0'",
            "ET.SubElement(ns,'recyclemaxsize').text='0'",
            "ET.SubElement(ns,'recyclemaxage').text='0'",
            "ET.SubElement(ns,'hidedotfiles').text='1'",
            "ET.SubElement(ns,'inheritacls').text='false'",
            "ET.SubElement(ns,'inheritpermissions').text='false'",
            "ET.SubElement(ns,'easupport').text='1'",
            "ET.SubElement(ns,'storedosattributes').text='0'",
            "ET.SubElement(ns,'hostsallow').text=''",
            "ET.SubElement(ns,'hostsdeny').text=''",
            "ET.SubElement(ns,'audit').text='0'",
            "ET.SubElement(ns,'timemachine').text='0'",
            "ET.SubElement(ns,'timemachinemaxsize').text=''",
            "ET.SubElement(ns,'transportencryption').text='0'",
            "ET.SubElement(ns,'followsymlinks').text='1'",
            "ET.SubElement(ns,'widelinks').text='0'",
            "ET.SubElement(ns,'extraoptions').text=''",
        ]
    lines += [
        "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)",
    ]
    sh.exec_python("\n".join(lines))

    if create_smb:
        code, out, err = sh.exec(OMV_SALT, timeout=120)
        if code != 0:
            return _err(f"omv-salt retornou {code}: {err[:200] if err else out[:200]}")
        sh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)

    return _ok(f"Pasta '{name}' criada")


@app.route("/pastas/<name>/editar", methods=["GET"])
@_login_required
def pasta_editar_form(name):
    folders = _load_folders()
    folder = next((f for f in folders if f["name"] == name), None)
    if not folder:
        return _err("Pasta não encontrada", 404)
    groups = [g["name"] for g in _load_omv_groups()]
    disks = _load_disks()
    return render_template("pasta_form.html", folder=folder, groups=groups,
                           disks=disks, all_groups=groups)


@app.route("/pastas/<name>/editar", methods=["POST"])
@_login_required
def pasta_editar(name):
    group = request.form.get("group", "").strip()
    perms = request.form.get("permissions", "").strip() or request.form.get("permissions_custom", "").strip()

    folders = _load_folders()
    folder = next((f for f in folders if f["name"] == name), None)
    if not folder:
        return _err("Pasta não encontrada", 404)
    path = folder["path"]

    if group:
        sh.exec_assert(f"chown root:'{group}' '{path}'")
    if perms:
        if not re.match(r"^\d{3,4}$", perms):
            return _err("Permissão deve ser 3-4 dígitos (ex: 770)")
        sh.exec_assert(f"chmod {perms} '{path}'")
        sh.exec_assert(f"chmod g+s '{path}'")
    return _ok("Pasta atualizada")


@app.route("/pastas/<name>/apagar", methods=["POST"])
@_login_required
def pasta_apagar(name):
    folders = _load_folders()
    folder = next((f for f in folders if f["name"] == name), None)
    if not folder:
        return _err("Pasta não encontrada", 404)
    path = folder["path"]

    n = base64.b64encode(name.encode()).decode()
    script = (
        "import xml.etree.ElementTree as ET, base64\n"
        "ET.register_namespace('', '')\n"
        f"n=base64.b64decode('{n}').decode()\n"
        "t=ET.parse('/etc/openmediavault/config.xml')\n"
        "r=t.getroot()\n"
        "sf=r.find(\".//sharedfolder[name='\"+n+\"']\")\n"
        "if sf is not None:\n"
        "    sf_uuid=sf.find('uuid')\n"
        "    sfref=sf_uuid.text if sf_uuid is not None else ''\n"
        "    for sh in r.findall(\".//share[sharedfolderref='\"+sfref+\"']\"):\n"
        "        parent=r.find('.//services/smb/shares')\n"
        "        if parent is not None and sh in list(parent):\n"
        "            parent.remove(sh)\n"
        "    p=r.find('.//system/shares')\n"
        "    if p is not None and sf in list(p):\n"
        "        p.remove(sf)\n"
        "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)\n"
    )
    sh.exec_python(script)
    sh.exec_assert(f"rm -rf '{path}'", timeout=30)
    sh.exec(OMV_SALT, timeout=120)
    sh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)
    return _ok("Pasta removida")


# ---------------------------------------------------------------------------
# ACLs
# ---------------------------------------------------------------------------


def _load_acls():
    acls = []
    try:
        root = _get_xml()
        for sf in root.findall(".//sharedfolder"):
            fname = (sf.findtext("name") or "").strip()
            privs = sf.find("privileges")
            if privs is None:
                continue
            for p in privs.findall("privilege"):
                acls.append({
                    "uuid": (p.findtext("uuid") or "").strip(),
                    "folder": fname,
                    "name": (p.findtext("name") or "").strip(),
                    "type": (p.findtext("type") or "").strip(),
                    "perms": (p.findtext("perms") or "").strip(),
                })
    except Exception:
        pass
    return acls


@app.route("/acls")
@_login_required
def acls():
    acls_list = _load_acls()
    folders = [f["name"] for f in _load_folders()]
    users = [u["username"] for u in _load_users()]
    return _htx("acls.html", acls=acls_list, folders=folders, users=users)


@app.route("/acls/novo", methods=["GET"])
@_login_required
def acl_novo_form():
    folders = [f["name"] for f in _load_folders()]
    users = [u["username"] for u in _load_users()]
    groups = [g["name"] for g in _load_omv_groups()]
    return render_template("acl_form.html", acl=None, folders=folders,
                           users=users, groups=groups)


@app.route("/acls/novo", methods=["POST"])
@_login_required
def acl_novo():
    folder = request.form.get("folder", "").strip()
    name = request.form.get("name", "").strip()
    tipo = request.form.get("type", "user").strip()
    perms = request.form.get("perms", "rwx").strip()

    if not folder or not name:
        return _err("Pasta e nome obrigatórios")
    if perms not in ("rwx", "rx", "r", "rw", "wx", "w", "x"):
        return _err("Permissão inválida")

    try:
        _add_acl(folder, name, tipo, perms)
        return _ok("ACL adicionada")
    except Exception as e:
        return _err(str(e))


@app.route("/acls/<uuid>/apagar", methods=["POST"])
@_login_required
def acl_apagar(uuid):
    try:
        _remove_acl(uuid)
        return _ok("ACL removida")
    except Exception as e:
        return _err(str(e))


def _add_acl(folder_name, name, tipo, perms):
    root = _get_xml()
    sf = root.find(f".//sharedfolder[name='{folder_name}']")
    if sf is None:
        raise ValueError(f"Pasta '{folder_name}' não encontrada")
    privs = sf.find("privileges")
    if privs is None:
        privs = ET.SubElement(sf, "privileges")
    p = ET.SubElement(privs, "privilege")
    ET.SubElement(p, "uuid").text = _uuid()
    ET.SubElement(p, "name").text = name
    ET.SubElement(p, "type").text = tipo
    ET.SubElement(p, "perms").text = perms
    _save_xml(root)


def _remove_acl(uuid):
    root = _get_xml()
    for privs in root.findall(".//privileges"):
        for p in list(privs):
            if (p.findtext("uuid") or "").strip() == uuid:
                privs.remove(p)
                _save_xml(root)
                return
    _save_xml(root)


# ---------------------------------------------------------------------------
# SMB SHARES
# ---------------------------------------------------------------------------


def _load_smb_shares():
    shares = []
    try:
        root = _get_xml()
        folder_map = {}
        for sf in root.findall(".//sharedfolder"):
            sfid = (sf.findtext("uuid") or "").strip()
            sfname = (sf.findtext("name") or "").strip()
            if sfid and sfname:
                folder_map[sfid] = sfname
        for sh in root.findall(".//services/smb/shares/share"):
            ref = (sh.findtext("sharedfolderref") or "").strip()
            shares.append({
                "uuid": (sh.findtext("uuid") or "").strip(),
                "folder": folder_map.get(ref, ref),
                "enable": (sh.findtext("enable") or "0").strip(),
                "comment": (sh.findtext("comment") or "").strip(),
                "guest": (sh.findtext("guest") or "no").strip(),
                "readonly": (sh.findtext("readonly") or "0").strip(),
                "browseable": (sh.findtext("browseable") or "true").strip(),
            })
    except Exception:
        pass
    return shares


@app.route("/smb")
@_login_required
def smb():
    shares = _load_smb_shares()
    return _htx("smb.html", shares=shares)


@app.route("/smb/novo", methods=["GET"])
@_login_required
def smb_novo_form():
    folders = [f["name"] for f in _load_folders()]
    return render_template("smb_form.html", share=None, folders=folders)


@app.route("/smb/novo", methods=["POST"])
@_login_required
def smb_novo():
    folder = request.form.get("folder", "").strip()
    comment = request.form.get("comment", "").strip()
    guest = request.form.get("guest", "no").strip()
    browseable = request.form.get("browseable", "true").strip()

    if not folder:
        return _err("Pasta obrigatória")

    try:
        _add_smb_share(folder, comment, guest, browseable)
        return _ok("Compartilhamento SMB criado")
    except Exception as e:
        return _err(str(e))


@app.route("/smb/<uuid>/editar", methods=["GET"])
@_login_required
def smb_editar_form(uuid):
    shares = _load_smb_shares()
    share = next((s for s in shares if s["uuid"] == uuid), None)
    if not share:
        return _err("Compartilhamento não encontrado", 404)
    folders = [f["name"] for f in _load_folders()]
    return render_template("smb_form.html", share=share, folders=folders)


@app.route("/smb/<uuid>/editar", methods=["POST"])
@_login_required
def smb_editar(uuid):
    comment = request.form.get("comment", "").strip()
    guest = request.form.get("guest", "no").strip()
    browseable = request.form.get("browseable", "true").strip()
    readonly = request.form.get("readonly", "0").strip()

    try:
        _update_smb_share(uuid, comment, guest, browseable, readonly)
        return _ok("Compartilhamento atualizado")
    except Exception as e:
        return _err(str(e))


@app.route("/smb/<uuid>/toggle", methods=["POST"])
@_login_required
def smb_toggle(uuid):
    try:
        _toggle_smb(uuid)
        return _ok("Status alterado")
    except Exception as e:
        return _err(str(e))


@app.route("/smb/<uuid>/apagar", methods=["POST"])
@_login_required
def smb_apagar(uuid):
    try:
        _remove_smb_share(uuid)
        return _ok("Compartilhamento removido")
    except Exception as e:
        return _err(str(e))


def _add_smb_share(folder_name, comment, guest, browseable):
    root = _get_xml()
    sf = root.find(f".//sharedfolder[name='{folder_name}']")
    if sf is None:
        raise ValueError(f"Pasta '{folder_name}' não encontrada")
    sfref = (sf.findtext("uuid") or "").strip()
    if not sfref:
        raise ValueError(f"Pasta '{folder_name}' sem uuid")

    smb = root.find(".//services/smb")
    if smb is None:
        svc = root.find(".//services")
        if svc is None:
            raise ValueError("Nó services não encontrado")
        smb = ET.SubElement(svc, "smb")
    sh = smb.find("shares")
    if sh is None:
        sh = ET.SubElement(smb, "shares")

    ns = ET.SubElement(sh, "share")
    ET.SubElement(ns, "uuid").text = _uuid()
    ET.SubElement(ns, "enable").text = "1"
    ET.SubElement(ns, "sharedfolderref").text = sfref
    ET.SubElement(ns, "comment").text = comment
    ET.SubElement(ns, "guest").text = guest
    ET.SubElement(ns, "readonly").text = "0"
    ET.SubElement(ns, "browseable").text = browseable
    for tag, val in (
        ("recyclebin", "0"), ("recyclemaxsize", "0"), ("recyclemaxage", "0"),
        ("hidedotfiles", "1"), ("inheritacls", "false"),
        ("inheritpermissions", "false"), ("easupport", "1"),
        ("storedosattributes", "0"), ("hostsallow", ""), ("hostsdeny", ""),
        ("audit", "0"), ("timemachine", "0"), ("timemachinemaxsize", ""),
        ("transportencryption", "0"), ("followsymlinks", "1"),
        ("widelinks", "0"), ("extraoptions", ""),
    ):
        ET.SubElement(ns, tag).text = val
    _save_xml(root)
    _deploy_smb()


def _update_smb_share(uuid, comment, guest, browseable, readonly):
    root = _get_xml()
    for sh in root.findall(".//services/smb/shares/share"):
        if (sh.findtext("uuid") or "").strip() == uuid:
            _set_or_create(sh, "comment", comment)
            _set_or_create(sh, "guest", guest)
            _set_or_create(sh, "browseable", browseable)
            _set_or_create(sh, "readonly", readonly)
            break
    _save_xml(root)
    _deploy_smb()


def _toggle_smb(uuid):
    root = _get_xml()
    for sh in root.findall(".//services/smb/shares/share"):
        if (sh.findtext("uuid") or "").strip() == uuid:
            current = (sh.findtext("enable") or "0").strip()
            _set_or_create(sh, "enable", "0" if current == "1" else "1")
            break
    _save_xml(root)
    _deploy_smb()


def _remove_smb_share(uuid):
    root = _get_xml()
    for sh in root.findall(".//services/smb/shares/share"):
        if (sh.findtext("uuid") or "").strip() == uuid:
            parent = root.find(".//services/smb/shares")
            if parent is not None:
                parent.remove(sh)
            break
    _save_xml(root)
    _deploy_smb()


def _set_or_create(parent, tag, value):
    el = parent.find(tag)
    if el is not None:
        el.text = value
    else:
        ET.SubElement(parent, tag).text = value


def _deploy_smb():
    sh.exec(OMV_SALT, timeout=120)
    sh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)


# ---------------------------------------------------------------------------
# EXPORT / IMPORT
# ---------------------------------------------------------------------------


@app.route("/exportar")
@_login_required
def exportar():
    data = {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "groups": _load_omv_groups(),
        "users": _load_users(),
        "folders": _load_folders(),
        "acls": _load_acls(),
        "smb_shares": _load_smb_shares(),
    }
    resp = make_response(json.dumps(data, indent=2, ensure_ascii=False))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=omv-backup.json"
    return resp


@app.route("/importar", methods=["POST"])
@_login_required
def importar():
    file = request.files.get("backup")
    if not file:
        return _err("Nenhum arquivo enviado")
    try:
        data = json.load(file)
    except Exception as e:
        return _err(f"Arquivo inválido: {e}")

    if not any(k in data for k in ("groups", "users", "folders")):
        return _err("Formato de backup inválido")

    ok = []
    errs = []

    for g in data.get("groups", []):
        name = g["name"]
        if sh.group_exists(name):
            ok.append(f"Grupo '{name}' já existe")
            continue
        try:
            sh.exec_assert(f"groupadd '{name}'")
            for m in g.get("members", []):
                sh.exec(f"usermod -aG '{name}' '{m}'")
            _register_omv_group(name, g.get("comment", ""))
            ok.append(f"Grupo '{name}' criado")
        except Exception as e:
            errs.append(f"Grupo '{name}': {e}")

    for u in data.get("users", []):
        username = u["username"]
        if sh.user_exists(username):
            ok.append(f"Usuário '{username}' já existe")
            continue
        try:
            primary = u.get("primary_group", "users")
            sh.exec_assert(f"useradd -m -g '{primary}' -s /bin/bash '{username}'")
            for eg in u.get("extra_groups", []):
                if eg.strip():
                    sh.exec(f"usermod -aG '{eg.strip()}' '{username}'")
            ok.append(f"Usuário '{username}' criado (definir senha manualmente)")
        except Exception as e:
            errs.append(f"Usuário '{username}': {e}")

    smb_needed = False
    for f in data.get("folders", []):
        name = f["name"]
        disk_mount = f.get("disk", "")
        group = f.get("group", "users")
        perms = f.get("permissions", "770")
        if perms.startswith("d"):
            perms = "770"
        root = _get_xml()
        m = root.find(f".//mntent[dir='{disk_mount}']")
        if m is None:
            errs.append(f"Pasta '{name}': disco '{disk_mount}' não encontrado")
            continue
        disk_uuid = (m.findtext("uuid") or "").strip()
        path = f"{disk_mount}/{name}"
        code, _, _ = sh.exec(f"test -d '{path}'")
        if code == 0:
            ok.append(f"Pasta '{name}' já existe")
            continue
        try:
            sh.exec_assert(f"mkdir -p '{path}'")
            sh.exec_assert(f"chown root:'{group}' '{path}'")
            sh.exec_assert(f"chmod {perms} '{path}'")
            sh.exec_assert(f"chmod g+s '{path}'")
            sf_uuid = _uuid()
            smb_uuid = _uuid()
            script = (
                "import xml.etree.ElementTree as ET\n"
                "ET.register_namespace('', '')\n"
                "t=ET.parse('/etc/openmediavault/config.xml')\n"
                "r=t.getroot()\n"
                "p=r.find('.//system/shares')\n"
                "if p is None:\n"
                "    sn=r.find('.//system')\n"
                "    if sn is None: raise Exception('no system')\n"
                "    p=ET.SubElement(sn,'shares')\n"
                "nf=ET.SubElement(p,'sharedfolder')\n"
                f"ET.SubElement(nf,'uuid').text='{sf_uuid}'\n"
                f"ET.SubElement(nf,'name').text='{name}'\n"
                "ET.SubElement(nf,'comment').text=''\n"
                f"ET.SubElement(nf,'mntentref').text='{disk_uuid}'\n"
                f"ET.SubElement(nf,'reldirpath').text='{name}/'\n"
                "# SMB\n"
                "smb=r.find('.//services/smb')\n"
                "if smb is None:\n"
                "    svc=r.find('.//services')\n"
                "    if svc is None: raise Exception('no services')\n"
                "    smb=ET.SubElement(svc,'smb')\n"
                "sh=smb.find('shares')\n"
                "if sh is None: sh=ET.SubElement(smb,'shares')\n"
                "ns=ET.SubElement(sh,'share')\n"
                f"ET.SubElement(ns,'uuid').text='{smb_uuid}'\n"
                "ET.SubElement(ns,'enable').text='1'\n"
                f"ET.SubElement(ns,'sharedfolderref').text='{sf_uuid}'\n"
                f"ET.SubElement(ns,'comment').text='{name}'\n"
                "ET.SubElement(ns,'guest').text='no'\n"
                "ET.SubElement(ns,'readonly').text='0'\n"
                "ET.SubElement(ns,'browseable').text='true'\n"
                "ET.SubElement(ns,'recyclebin').text='0'\n"
                "ET.SubElement(ns,'recyclemaxsize').text='0'\n"
                "ET.SubElement(ns,'recyclemaxage').text='0'\n"
                "ET.SubElement(ns,'hidedotfiles').text='1'\n"
                "ET.SubElement(ns,'inheritacls').text='false'\n"
                "ET.SubElement(ns,'inheritpermissions').text='false'\n"
                "ET.SubElement(ns,'easupport').text='1'\n"
                "ET.SubElement(ns,'storedosattributes').text='0'\n"
                "ET.SubElement(ns,'hostsallow').text=''\n"
                "ET.SubElement(ns,'hostsdeny').text=''\n"
                "ET.SubElement(ns,'audit').text='0'\n"
                "ET.SubElement(ns,'timemachine').text='0'\n"
                "ET.SubElement(ns,'timemachinemaxsize').text=''\n"
                "ET.SubElement(ns,'transportencryption').text='0'\n"
                "ET.SubElement(ns,'followsymlinks').text='1'\n"
                "ET.SubElement(ns,'widelinks').text='0'\n"
                "ET.SubElement(ns,'extraoptions').text=''\n"
                "t.write('/etc/openmediavault/config.xml',"
                "encoding='UTF-8',xml_declaration=True)\n"
            )
            sh.exec_python(script)
            smb_needed = True
            ok.append(f"Pasta '{name}' criada")
        except Exception as e:
            errs.append(f"Pasta '{name}': {e}")

    if smb_needed:
        sh.exec(OMV_SALT, timeout=120)
        sh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)

    # ACLs
    for a in data.get("acls", []):
        folder = a.get("folder", "")
        name = a.get("name", "")
        tipo = a.get("type", "user")
        perms = a.get("perms", "rwx")
        if not folder or not name:
            continue
        try:
            _add_acl(folder, name, tipo, perms)
            ok.append(f"ACL '{name}' em '{folder}'")
        except Exception as e:
            errs.append(f"ACL '{name}': {e}")

    # SMB shares
    for s in data.get("smb_shares", []):
        folder = s.get("folder", "")
        if not folder:
            continue
        # Check if already exists
        existing = _load_smb_shares()
        if any(x["folder"] == folder for x in existing):
            ok.append(f"SMB '{folder}' já existe")
            continue
        try:
            _add_smb_share(
                folder,
                s.get("comment", ""),
                s.get("guest", "no"),
                s.get("browseable", "true"),
            )
            ok.append(f"SMB '{folder}' criado")
        except Exception as e:
            errs.append(f"SMB '{folder}': {e}")

    msg = f"{len(ok)} sucesso(s)"
    if errs:
        msg += f", {len(errs)} erro(s)"
    status = "success" if not errs else "warning"
    return _ok(msg, extra={"ok": ok, "errs": errs})


# ---------------------------------------------------------------------------
# Helpers for HTMX responses
# ---------------------------------------------------------------------------


def _err(msg, code=422):
    resp = make_response(
        f'<div class="alert alert-danger alert-dismissible fade show" '
        f'role="alert">{msg}<button type="button" class="btn-close" '
        f'data-bs-dismiss="alert"></button></div>',
        code,
    )
    resp.headers["HX-Retarget"] = "#toast-area"
    return resp


def _ok(msg, extra=None):
    resp = make_response(
        f'<div class="alert alert-success alert-dismissible fade show" '
        f'role="alert">{msg}<button type="button" class="btn-close" '
        f'data-bs-dismiss="alert"></button></div>',
    )
    resp.headers["HX-Retarget"] = "#toast-area"
    resp.headers["HX-Trigger"] = "refreshList"
    return resp


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("OMV_WEB_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
