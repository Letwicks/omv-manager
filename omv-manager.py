#!/usr/bin/env python3
"""
OMV Manager - Gerenciamento de Usuários, Pastas e Permissões no OpenMediaVault

Uso:
    python omv-manager.py --config config.yaml --apply
    python omv-manager.py --config config.yaml --dry-run
    python omv-manager.py --config config.yaml --status
    python omv-manager.py --help
"""

import argparse
import base64
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import yaml

try:
    import paramiko
except ImportError:
    print("Erro: instale as dependências com: pip install -r requirements.txt")
    sys.exit(1)


# ===========================================================================
# CORES (ANSI)
# ===========================================================================

class Color:
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    CYAN = '\033[0;36m'
    RED = '\033[0;31m'
    NC = '\033[0m'

    @classmethod
    def info(cls, msg):    print(f"{cls.GREEN}[INFO]{cls.NC} {msg}")
    @classmethod
    def warn(cls, msg):    print(f"{cls.YELLOW}[WARN]{cls.NC} {msg}")
    @classmethod
    def step(cls, msg):    print(f"{cls.CYAN}[STEP]{cls.NC} {msg}")
    @classmethod
    def error(cls, msg):   print(f"{cls.RED}[ERROR]{cls.NC} {msg}")
    @classmethod
    def dry(cls, msg):     print(f"{cls.YELLOW}[DRY-RUN]{cls.NC} {msg}")


# ===========================================================================
# CONFIG
# ===========================================================================

@dataclass
class ServerConfig:
    host: str = ""
    port: int = 22
    username: str = "root"
    password: str = ""


@dataclass
class Settings:
    base_path: str = "/srv/dev-disk-by-uuid-cdc81bc1-9210-48b1-a342-28f7ce43fdd0"
    mntentref: str = "3fe14629-114d-440c-aa00-56581fd52ef5"
    workgroup: str = "WORKGROUP"


@dataclass
class UserConfig:
    nome: str
    senha: str = ""
    grupo_primario: str = ""
    grupos_adicionais: list = field(default_factory=list)
    email: str = ""


@dataclass
class GroupConfig:
    nome: str
    comentario: str = ""
    membros: list = field(default_factory=list)


@dataclass
class FolderConfig:
    nome: str
    grupo: str = "users"
    permissao: str = "770"


@dataclass
class ACLConfig:
    pasta: str
    tipo: str  # user | group
    alvo: str
    permissao: str  # rwx, rx, r, etc


@dataclass
class SMBShareConfig:
    nome: str
    comentario: str = ""
    guest: str = "no"
    browseable: bool = True
    inherit_acl: bool = False


@dataclass
class OMVConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    settings: Settings = field(default_factory=Settings)
    users: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    folders: list = field(default_factory=list)
    acls: list = field(default_factory=list)
    smb_shares: list = field(default_factory=list)


def load_config(path: str) -> OMVConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    cfg = OMVConfig()

    srv = raw.get("server", {})
    cfg.server = ServerConfig(
        host=srv.get("host", ""),
        port=srv.get("port", 22),
        username=srv.get("username", "root"),
        password=srv.get("password", ""),
    )

    st = raw.get("settings", {})
    cfg.settings = Settings(
        base_path=st.get("base_path", cfg.settings.base_path),
        mntentref=st.get("mntentref", cfg.settings.mntentref),
        workgroup=st.get("workgroup", cfg.settings.workgroup),
    )

    for u in raw.get("users", []):
        cfg.users.append(UserConfig(
            nome=u.get("nome", ""),
            senha=u.get("senha", ""),
            grupo_primario=u.get("grupo_primario", ""),
            grupos_adicionais=u.get("grupos_adicionais", []),
            email=u.get("email", ""),
        ))

    for g in raw.get("groups", []):
        cfg.groups.append(GroupConfig(
            nome=g.get("nome", ""),
            comentario=g.get("comentario", ""),
            membros=g.get("membros", []),
        ))

    for f in raw.get("folders", []):
        cfg.folders.append(FolderConfig(
            nome=f.get("nome", ""),
            grupo=f.get("grupo", "users"),
            permissao=f.get("permissao", "770"),
        ))

    for a in raw.get("acls", []):
        cfg.acls.append(ACLConfig(
            pasta=a.get("pasta", ""),
            tipo=a.get("tipo", ""),
            alvo=a.get("alvo", ""),
            permissao=a.get("permissao", ""),
        ))

    for s in raw.get("smb_shares", []):
        cfg.smb_shares.append(SMBShareConfig(
            nome=s.get("nome", ""),
            comentario=s.get("comentario", ""),
            guest=s.get("guest", "no"),
            browseable=s.get("browseable", True),
            inherit_acl=s.get("inherit_acl", False),
        ))

    return cfg


# ===========================================================================
# SSH
# ===========================================================================

class SSHClient:
    def __init__(self, config: ServerConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.client: Optional[paramiko.SSHClient] = None

    def connect(self):
        if self.dry_run:
            return
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(
                self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                timeout=15,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            Color.error(f"Falha de autenticação em {self.config.host}")
            sys.exit(1)
        except paramiko.SSHException as e:
            Color.error(f"Falha SSH: {e}")
            sys.exit(1)
        except Exception as e:
            Color.error(f"Erro de conexão: {e}")
            sys.exit(1)
        Color.info(f"Conectado a {self.config.host}")

    def close(self):
        if self.client:
            self.client.close()

    def exec(self, command: str, sudo: bool = False, timeout: int = 60) -> tuple[int, str, str]:
        if self.dry_run:
            Color.dry(command[:200])
            return (0, "", "")

        if sudo and self.config.username != "root":
            command = f"sudo {command}"

        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            exit_code = stdout.channel.recv_exit_status()
            return (exit_code, out, err)
        except Exception as e:
            return (-1, "", str(e))

    def exec_assert(self, command: str, sudo: bool = False, timeout: int = 60) -> str:
        code, out, err = self.exec(command, sudo, timeout)
        if code != 0:
            if err:
                Color.warn(f"Comando retornou {code}: {err[:200]}")
        return out

    def write_remote_file_sftp(self, remote_path: str, content: str):
        if self.dry_run:
            Color.dry(f"SFTP escrever {remote_path} ({len(content)} bytes)")
            return
        try:
            sftp = self.client.open_sftp()
            with sftp.open(remote_path, 'w') as f:
                f.write(content)
            sftp.close()
        except Exception as e:
            Color.warn(f"SFTP falhou ({e}), tentando via shell...")
            encoded = base64.b64encode(content.encode()).decode()
            self.exec(f"python3 -c \"import base64; open('{remote_path}','w').write(base64.b64decode('{encoded}').decode())\"", timeout=15)

    def exec_python(self, code: str) -> tuple[int, str, str]:
        remote_path = f"/tmp/omv_{uuid.uuid4().hex[:8]}.py"
        self.write_remote_file_sftp(remote_path, code)
        code_exit, out, err = self.exec(f"python3 {remote_path}", timeout=60)
        self.exec(f"rm -f {remote_path}", timeout=5)
        return (code_exit, out, err)

    def file_exists(self, path: str) -> bool:
        code, out, _ = self.exec(f"test -e '{path}' && echo YES || echo NO")
        return "YES" in out

    def user_exists(self, name: str) -> bool:
        code, out, _ = self.exec(f"id '{name}' 2>/dev/null && echo YES || echo NO")
        return "YES" in out

    def group_exists(self, name: str) -> bool:
        code, out, _ = self.exec(f"getent group '{name}' >/dev/null 2>&1 && echo YES || echo NO")
        return "YES" in out

    def dir_exists(self, path: str) -> bool:
        code, out, _ = self.exec(f"test -d '{path}' && echo YES || echo NO")
        return "YES" in out

    def samba_user_exists(self, name: str) -> bool:
        code, out, _ = self.exec(f"pdbedit -L 2>/dev/null | grep -q '^{name}:' && echo YES || echo NO")
        return "YES" in out

    def xml_exists(self, xpath: str) -> bool:
        # Simple grep-based check on config.xml
        import re
        m = re.search(r"\[(\w+)='([^']+)'\]", xpath)
        if m:
            field, value = m.group(1), m.group(2)
            cmd = f"grep -c '<{field}>{value}</{field}>' /etc/openmediavault/config.xml"
            _, out, _ = self.exec(cmd, timeout=5)
            try:
                return int(out.strip()) > 0
            except ValueError:
                pass
        return False


# ===========================================================================
# OMV MANAGER
# ===========================================================================

class OMVManager:
    PERM_MAP = {
        "---": "0", "--x": "1", "-w-": "2", "-wx": "3",
        "r--": "4", "r-x": "5", "rw-": "6", "rwx": "7",
    }

    def __init__(self, cfg: OMVConfig, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.ssh = SSHClient(cfg.server, dry_run)

    # --- helpers ---

    @staticmethod
    def perm_to_num(perm: str) -> str:
        return OMVManager.PERM_MAP.get(perm, "0")

    @staticmethod
    def uuid_gen() -> str:
        return str(uuid.uuid4())

    def _acl_perm_to_num(self, perm: str) -> int:
        n = 0
        if "r" in perm: n += 4
        if "w" in perm: n += 2
        if "x" in perm: n += 1
        return n

    # ======================================================================
    # GRUPOS
    # ======================================================================

    def create_groups(self):
        Color.step("Criando grupos no sistema...")
        for g in self.cfg.groups:
            if self.ssh.group_exists(g.nome):
                Color.info(f"Grupo '{g.nome}' já existe no sistema")
            else:
                self.ssh.exec_assert(f"groupadd '{g.nome}'")
                Color.info(f"Grupo '{g.nome}' criado no sistema")
            for m in g.membros:
                if self.ssh.user_exists(m):
                    self.ssh.exec_assert(f"usermod -aG '{g.nome}' '{m}'")
                    Color.info(f"  Usuário '{m}' adicionado ao grupo '{g.nome}'")

    def register_omv_groups(self):
        Color.step("Registrando grupos no OMV...")
        self._pending_groups = []
        for g in self.cfg.groups:
            xpath = f"//config/system/usermanagement/groups/group[name='{g.nome}']"
            if self.ssh.xml_exists(xpath):
                Color.info(f"Grupo OMV '{g.nome}' já registrado")
            else:
                self._pending_groups.append(g)
        if self._pending_groups:
            self._generate_and_run_xml_script()

    def _generate_and_run_xml_script(self):
        """Generate and execute a single Python script that does all pending XML modifications."""
        lines = [
            "import xml.etree.ElementTree as ET",
            "import sys",
            "ET.register_namespace('', '')",
            "tree = ET.parse('/etc/openmediavault/config.xml')",
            "root = tree.getroot()",
        ]

        # Add pending groups
        for g in getattr(self, '_pending_groups', []):
            uid = self.uuid_gen()
            lines += [
                f"p = root.find('.//system/usermanagement/groups')",
                f"n = ET.SubElement(p, 'group')",
                f"ET.SubElement(n, 'uuid').text = '{uid}'",
                f"ET.SubElement(n, 'name').text = '{g.nome}'",
                f"ET.SubElement(n, 'comment').text = '{g.comentario}'",
                f"sys.stderr.write('Grupo OMV \\\"{g.nome}\\\" registrado\\\\n')",
            ]

        # Add pending users
        for u in getattr(self, '_pending_users', []):
            uid = self.uuid_gen()
            lines += [
                f"p = root.find('.//system/usermanagement/users')",
                f"n = ET.SubElement(p, 'user')",
                f"ET.SubElement(n, 'uuid').text = '{uid}'",
                f"ET.SubElement(n, 'name').text = '{u.nome}'",
                f"ET.SubElement(n, 'email').text = '{u.email}'",
                f"ET.SubElement(n, 'disallowusermod').text = '0'",
                f"sk = ET.SubElement(n, 'sshpubkeys')",
                f"ET.SubElement(sk, 'sshpubkey')",
                f"sys.stderr.write('Usuário OMV \\\"{u.nome}\\\" registrado\\\\n')",
            ]

        # Add pending shared folders
        base = self.cfg.settings.base_path
        mnt = self.cfg.settings.mntentref
        for f in getattr(self, '_pending_sf', []):
            uid = self.uuid_gen()
            privs_xml = ""
            for a in self.cfg.acls:
                if a.pasta == f.nome:
                    pn = self._acl_perm_to_num(a.permissao)
                    privs_xml += f"<privilege><type>{a.tipo}</type><name>{a.alvo}</name><perms>{pn}</perms></privilege>"
            esc_privs = privs_xml.replace("'", "\\'")
            lines += [
                f"p = root.find('.//system/shares')",
                f"n = ET.SubElement(p, 'sharedfolder')",
                f"ET.SubElement(n, 'uuid').text = '{uid}'",
                f"ET.SubElement(n, 'name').text = '{f.nome}'",
                f"ET.SubElement(n, 'comment').text = ''",
                f"ET.SubElement(n, 'mntentref').text = '{mnt}'",
                f"ET.SubElement(n, 'reldirpath').text = '{f.nome}/'",
            ]
            if esc_privs:
                lines += [
                    f"pp = ET.SubElement(n, 'privileges')",
                    f"for pt in '''{esc_privs}'''.split('</privilege>'):",
                    f"    t = pt.strip()",
                    f"    if t:",
                    f"        try: pp.append(ET.fromstring(t + '</privilege>'))",
                    f"        except: pass",
                ]
            lines.append(f"sys.stderr.write('Sharedfolder OMV \\\"{f.nome}\\\" registrado\\\\n')")

        # Add pending SMB shares
        for s in getattr(self, '_pending_smb', []):
            uid = self.uuid_gen()
            sf_uuid = self._get_sf_uuid(s.nome)
            if not sf_uuid:
                continue
            b_browse = "true" if s.browseable else "false"
            b_inherit = "true" if s.inherit_acl else "false"
            lines += [
                f"p = root.find('.//services/smb/shares')",
                f"if p is None:",
                f"    smb = root.find('.//services/smb')",
                f"    if smb is None: sys.exit(1)",
                f"    p = ET.SubElement(smb, 'shares')",
                f"n = ET.SubElement(p, 'share')",
                f"ET.SubElement(n, 'uuid').text = '{uid}'",
                f"ET.SubElement(n, 'enable').text = '1'",
                f"ET.SubElement(n, 'sharedfolderref').text = '{sf_uuid}'",
                f"ET.SubElement(n, 'comment').text = '{s.comentario}'",
                f"ET.SubElement(n, 'guest').text = '{s.guest}'",
                f"ET.SubElement(n, 'readonly').text = '0'",
                f"ET.SubElement(n, 'browseable').text = '{b_browse}'",
                f"ET.SubElement(n, 'recyclebin').text = '0'",
                f"ET.SubElement(n, 'recyclemaxsize').text = '0'",
                f"ET.SubElement(n, 'recyclemaxage').text = '0'",
                f"ET.SubElement(n, 'hidedotfiles').text = '1'",
                f"ET.SubElement(n, 'inheritacls').text = '{b_inherit}'",
                f"ET.SubElement(n, 'inheritpermissions').text = '{b_inherit}'",
                f"ET.SubElement(n, 'easupport').text = '1'",
                f"ET.SubElement(n, 'storedosattributes').text = '0'",
                f"ET.SubElement(n, 'hostsallow').text = ''",
                f"ET.SubElement(n, 'hostsdeny').text = ''",
                f"ET.SubElement(n, 'audit').text = '0'",
                f"ET.SubElement(n, 'timemachine').text = '0'",
                f"ET.SubElement(n, 'timemachinemaxsize').text = ''",
                f"ET.SubElement(n, 'transportencryption').text = '0'",
                f"ET.SubElement(n, 'followsymlinks').text = '1'",
                f"ET.SubElement(n, 'widelinks').text = '0'",
                f"ET.SubElement(n, 'extraoptions').text = ''",
                f"sys.stderr.write('SMB share \\\"{s.nome}\\\" criado\\\\n')",
            ]

        lines += [
            "tree.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)",
        ]

        script = "\n".join(lines)
        _, out, err = self.ssh.exec_python(script)
        if err:
            for line in err.replace("\\n", "\n").split("\n"):
                line = line.strip()
                if line:
                    Color.info(line)

        self._pending_groups = []
        self._pending_users = []
        self._pending_sf = []
        self._pending_smb = []

    # ======================================================================
    # USUÁRIOS
    # ======================================================================

    def create_users(self):
        Color.step("Criando usuários no sistema...")
        for u in self.cfg.users:
            if self.ssh.user_exists(u.nome):
                Color.info(f"Usuário '{u.nome}' já existe no sistema")
            else:
                cmd = f"useradd -m -s /bin/bash -c '{u.nome}' '{u.nome}'"
                if u.grupo_primario and self.ssh.group_exists(u.grupo_primario):
                    cmd = f"useradd -m -g '{u.grupo_primario}' -s /bin/bash -c '{u.nome}' '{u.nome}'"
                self.ssh.exec_assert(cmd)
                if u.senha:
                    self.ssh.exec_assert(f"echo '{u.nome}:{u.senha}' | chpasswd")
                Color.info(f"Usuário '{u.nome}' criado no sistema")

            for eg in u.grupos_adicionais:
                if eg and self.ssh.group_exists(eg):
                    self.ssh.exec_assert(f"usermod -aG '{eg}' '{u.nome}'")
                    Color.info(f"  Usuário '{u.nome}' adicionado ao grupo '{eg}'")

            if not self.ssh.samba_user_exists(u.nome) and u.senha:
                cmd = f"(echo '{u.senha}'; echo '{u.senha}') | smbpasswd -a -s '{u.nome}'"
                self.ssh.exec_assert(cmd)
                Color.info(f"  Senha Samba definida para '{u.nome}'")

    def register_omv_users(self):
        Color.step("Registrando usuários no OMV...")
        self._pending_users = []
        for u in self.cfg.users:
            xpath = f"//config/system/usermanagement/users/user[name='{u.nome}']"
            if self.ssh.xml_exists(xpath):
                Color.info(f"Usuário OMV '{u.nome}' já registrado")
            else:
                self._pending_users.append(u)
        if self._pending_users:
            self._generate_and_run_xml_script()

    # ======================================================================
    # PASTAS
    # ======================================================================

    def create_folders(self):
        Color.step("Criando pastas compartilhadas...")
        base = self.cfg.settings.base_path
        for f in self.cfg.folders:
            path = f"{base}/{f.nome}"
            if self.ssh.dir_exists(path):
                Color.info(f"Pasta '{path}' já existe")
            else:
                self.ssh.exec_assert(f"mkdir -p '{path}'")
                Color.info(f"Pasta '{path}' criada")
            if self.ssh.group_exists(f.grupo):
                self.ssh.exec_assert(f"chown root:'{f.grupo}' '{path}'")
            self.ssh.exec_assert(f"chmod {f.permissao} '{path}'")
            self.ssh.exec_assert(f"chmod g+s '{path}'")
            Color.info(f"  Permissões: {f.permissao} + setgid")

    # ======================================================================
    # ACLs
    # ======================================================================

    def set_acls(self):
        Color.step("Configurando ACLs...")
        base = self.cfg.settings.base_path
        for a in self.cfg.acls:
            path = f"{base}/{a.pasta}"
            if not self.ssh.dir_exists(path):
                Color.warn(f"Pasta '{path}' não existe, pulando ACL")
                continue
            p_val = self._acl_perm_to_num(a.permissao)
            if a.tipo == "user":
                if not self.ssh.user_exists(a.alvo):
                    Color.warn(f"  Usuário '{a.alvo}' não existe, pulando ACL")
                    continue
                self.ssh.exec_assert(f"setfacl -m u:{a.alvo}:{p_val} '{path}'")
                self.ssh.exec(f"setfacl -d -m u:{a.alvo}:{p_val} '{path}' 2>/dev/null")
                Color.info(f"  ACL user '{a.alvo}' = {a.permissao} ({p_val}) em '{a.pasta}'")
            elif a.tipo == "group":
                if not self.ssh.group_exists(a.alvo):
                    Color.warn(f"  Grupo '{a.alvo}' não existe, pulando ACL")
                    continue
                self.ssh.exec_assert(f"setfacl -m g:{a.alvo}:{p_val} '{path}'")
                self.ssh.exec(f"setfacl -d -m g:{a.alvo}:{p_val} '{path}' 2>/dev/null")
                Color.info(f"  ACL group '{a.alvo}' = {a.permissao} ({p_val}) em '{a.pasta}'")

    # ======================================================================
    # SHARED FOLDERS (OMV)
    # ======================================================================

    def register_omv_sharedfolders(self):
        Color.step("Registrando pastas compartilhadas no OMV...")
        self._pending_sf = []
        for f in self.cfg.folders:
            xpath = f"//config/system/shares/sharedfolder[name='{f.nome}']"
            if self.ssh.xml_exists(xpath):
                Color.info(f"Sharedfolder OMV '{f.nome}' já registrado")
            else:
                self._pending_sf.append(f)
        if self._pending_sf:
            self._generate_and_run_xml_script()

    # ======================================================================
    # SMB SHARES
    # ======================================================================

    def _get_sf_uuid(self, name: str) -> str:
        script = f"""import xml.etree.ElementTree as ET
t = ET.parse('/etc/openmediavault/config.xml')
r = t.findall(".//sharedfolder[name='{name}']/uuid")
print(r[0].text if r else '')
"""
        _, out, _ = self.ssh.exec_python(script)
        return out.strip()

    def create_smb_shares(self):
        Color.step("Criando compartilhamentos SMB no OMV...")
        self._pending_smb = []
        for s in self.cfg.smb_shares:
            sf_uuid = self._get_sf_uuid(s.nome)
            if not sf_uuid:
                Color.warn(f"Sharedfolder '{s.nome}' não encontrado no OMV, pulando SMB share")
                continue
            xpath = f"//config/services/smb/shares/share[sharedfolderref='{sf_uuid}']"
            if self.ssh.xml_exists(xpath):
                Color.info(f"SMB share '{s.nome}' já existe")
            else:
                self._pending_smb.append(s)
        if self._pending_smb:
            self._generate_and_run_xml_script()

    # ======================================================================
    # APPLY
    # ======================================================================

    def apply_config(self):
        Color.step("Aplicando configurações com omv-salt...")
        self.ssh.exec_assert("omv-salt deploy run samba 2>&1 | tail -20", timeout=120)
        Color.info("Configuração aplicada. Reiniciando serviços Samba...")
        self.ssh.exec("systemctl restart smbd nmbd 2>/dev/null || true")
        Color.info("Samba reiniciado com sucesso!")

    # ======================================================================
    # STATUS
    # ======================================================================

    def show_status(self):
        Color.step("STATUS DO SISTEMA")
        print(f"\n{Color.CYAN}========== USUÁRIOS =========={Color.NC}")
        _, out, _ = self.ssh.exec("getent passwd | cut -d: -f1,3,6,7 | sort -t: -k2 -n")
        if out:
            for line in out.split("\n"):
                parts = line.split(":")
                if len(parts) >= 4:
                    u, uid, home, shell = parts[0], parts[1], parts[2], parts[3]
                    grupos = self.ssh.exec_assert(f"groups '{u}' 2>/dev/null | cut -d: -f2")
                    print(f"  {u:20} UID:{uid:5}  {grupos[:60]}")

        print(f"\n{Color.CYAN}========== GRUPOS =========={Color.NC}")
        _, out, _ = self.ssh.exec("getent group | grep -v '^_:' | grep -v '^systemd-' | sort -t: -k3 -n")
        if out:
            for line in out.split("\n"):
                parts = line.split(":")
                if len(parts) >= 4:
                    g, gid, members = parts[0], parts[2], parts[3]
                    print(f"  {g:20} GID:{gid:5}  {members[:50]}")

        print(f"\n{Color.CYAN}========== PASTAS COMPARTILHADAS =========={Color.NC}")
        base = self.cfg.settings.base_path
        for f in self.cfg.folders:
            path = f"{base}/{f.nome}"
            if self.ssh.dir_exists(path):
                _, out, _ = self.ssh.exec(f"stat -c '%A %U:%G' '{path}' 2>/dev/null")
                print(f"  {Color.GREEN}{path}{Color.NC}  ({out})")
            else:
                print(f"  {Color.RED}{path}{Color.NC}  (NÃO EXISTE)")

        print(f"\n{Color.CYAN}========== SHARES SMB =========={Color.NC}")
        _, out, _ = self.ssh.exec("cat /etc/samba/smb.conf | grep -E '^\\[' | tr -d '[]'")
        if out:
            for line in out.splitlines():
                print(f"  {line.strip()}")

    # ======================================================================
    # EXECUÇÃO COMPLETA
    # ======================================================================

    def run_all(self):
        print(f"\n{'='*50}")
        Color.info("Iniciando gerenciamento OMV...")
        print(f"{'='*50}")

        self.ssh.connect()
        try:
            self.create_groups()
            print()
            self.register_omv_groups()
            print()
            self.create_users()
            print()
            self.register_omv_users()
            print()
            self.create_folders()
            print()
            self.set_acls()
            print()
            self.register_omv_sharedfolders()
            print()
            self.create_smb_shares()
            print()
            if not self.dry_run:
                self.apply_config()
            else:
                Color.info("DRY-RUN: omv-salt deploy run samba (pulado)")
            print()
            Color.info("Gerenciamento concluído com sucesso!")
            print(f"{'='*50}")
        finally:
            self.ssh.close()


# ===========================================================================
# CLI
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="OMV Manager - Gerenciamento de Usuários, Pastas e Permissões",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python omv-manager.py --config config.yaml --apply
  python omv-manager.py --config config.yaml --dry-run
  python omv-manager.py --config config.yaml --status
        """,
    )
    parser.add_argument("--config", "-c", default="config.yaml", help="Arquivo de configuração YAML")
    parser.add_argument("--apply", action="store_true", help="Executar todas as operações")
    parser.add_argument("--dry-run", action="store_true", help="Simular sem alterar nada")
    parser.add_argument("--status", action="store_true", help="Mostrar status atual")
    args = parser.parse_args()

    if not args.apply and not args.dry_run and not args.status:
        parser.print_help()
        print("\nUse --apply, --dry-run ou --status para executar.")
        sys.exit(0)

    if not os.path.exists(args.config):
        Color.error(f"Arquivo de configuração não encontrado: {args.config}")
        sys.exit(1)

    cfg = load_config(args.config)

    if args.dry_run:
        Color.info("MODO DRY-RUN - Nenhuma alteração será feita")
        m = OMVManager(cfg, dry_run=True)
        m.run_all()
    elif args.status:
        m = OMVManager(cfg)
        m.ssh.connect()
        try:
            m.show_status()
        finally:
            m.ssh.close()
    elif args.apply:
        m = OMVManager(cfg)
        m.run_all()


if __name__ == "__main__":
    main()
