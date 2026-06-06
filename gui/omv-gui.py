#!/usr/bin/env python3
"""
OMV Manager - Interface Gráfica (Tkinter)
"""

import os
import sys
import re
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import importlib.util
import base64
import socket
import time

_OMV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cli", "omv-manager.py")
_spec = importlib.util.spec_from_file_location("omv_manager_mod", _OMV_PATH)
_omv_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_omv_mod)
ServerConfig = _omv_mod.ServerConfig
SSHClient = _omv_mod.SSHClient

BG = "#1e1e2e"
FG = "#cdd6f4"
BTN_BG = "#313244"
BTN_FG = "#cdd6f4"
ACCENT = "#89b4fa"
GREEN = "#a6e3a1"
YELLOW = "#f9e2af"
CYAN = "#89dceb"
RED = "#f38ba8"
SURFACE = "#181825"
FONT = ("Consolas", 10)
FONT_BOLD = ("Consolas", 10, "bold")
FONT_TITLE = ("Consolas", 12, "bold")
FONT_SMALL = ("Consolas", 9)

_CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "omv-creds.json")


def _load_creds():
    try:
        import json
        with open(_CREDS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_creds(host, port, user, password):
    import json
    try:
        with open(_CREDS_FILE, "w") as f:
            json.dump({"host": host, "port": port, "user": user, "password": password}, f)
    except Exception:
        pass


def _clear_creds():
    try:
        os.remove(_CREDS_FILE)
    except Exception:
        pass


def _ssh_detect_base_path(ssh):
    try:
        _, out, _ = ssh.exec(
            """python3 -c "
import xml.etree.ElementTree as ET
t = ET.parse('/etc/openmediavault/config.xml')
m = t.find('.//mntent/dir')
print(m.text.strip() if m is not None else '')
" """, timeout=15)
        path = out.strip()
        if path:
            return path
        _, out, _ = ssh.exec("ls -1d /srv/*/ 2>/dev/null | head -1", timeout=10)
        path = out.strip().rstrip("/")
        if path:
            return path
    except Exception:
        pass
    return "/srv"


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


_PERM_PRESETS = [
    ("770", "Dono e grupo: acesso total / Outros: nenhum"),
    ("755", "Dono: acesso total / Grupo+Outros: ler e executar"),
    ("750", "Dono: acesso total / Grupo: ler e executar / Outros: nenhum"),
    ("777", "Todos: acesso total"),
    ("700", "Apenas dono: acesso total"),
]


def _style_btn(parent, text, command, fg, width=None):
    kwargs = dict(
        text=text, command=command, font=FONT_BOLD, bg=BTN_BG,
        fg=fg, activebackground=fg, activeforeground=BG,
        relief=tk.FLAT, padx=14, pady=4, cursor="hand2",
    )
    if width:
        kwargs["width"] = width
    return tk.Button(parent, **kwargs)


def _style_entry(parent, width=30, show=None):
    return tk.Entry(
        parent, font=FONT, bg=SURFACE, fg=FG, insertbackground=FG,
        relief=tk.FLAT, bd=2, width=width, show=show,
    )


def _style_label(parent, text, font=FONT, fg=FG):
    return tk.Label(parent, text=text, font=font, bg=BG, fg=fg)


# ===========================================================================
# DIALOGS
# ===========================================================================

class ConnectionDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Conectar ao Servidor")
        self.result = None
        self.running = False
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.minsize(420, 280)
        self.geometry("420x300+{}+{}".format(
            parent.winfo_rootx() + 80, parent.winfo_rooty() + 80
        ))

    def _build(self):
        pad = 12
        self.columnconfigure(0, weight=1)

        _style_label(self, "Conectar ao servidor OMV", FONT_TITLE, ACCENT) \
            .grid(row=0, column=0, pady=(pad, 4), padx=pad, sticky="w")

        frm = tk.Frame(self, bg=BG)
        frm.grid(row=1, column=0, sticky="ew", padx=pad, pady=4)
        frm.columnconfigure(1, weight=1)

        r = 0
        _style_label(frm, "Host / IP:").grid(row=r, column=0, sticky="w", pady=3)
        host_frm = tk.Frame(frm, bg=BG)
        host_frm.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        host_frm.columnconfigure(0, weight=1)
        self.entry_host = _style_entry(host_frm)
        self.entry_host.grid(row=0, column=0, sticky="ew")
        self.btn_buscar = _style_btn(host_frm, "Buscar", self._on_scan, YELLOW)
        self.btn_buscar.grid(row=0, column=1, padx=(6, 0))
        r += 1

        _style_label(frm, "Porta:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_port = _style_entry(frm, width=10)
        self.entry_port.grid(row=r, column=1, sticky="w", pady=3, padx=(8, 0))
        self.entry_port.insert(0, "22")
        r += 1

        _style_label(frm, "Usuário:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_user = _style_entry(frm)
        self.entry_user.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        self.entry_user.insert(0, "root")
        r += 1

        _style_label(frm, "Senha:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_pass = _style_entry(frm, show="*")
        self.entry_pass.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        r += 1

        self.save_var = tk.BooleanVar(value=False)
        self.chk_save = tk.Checkbutton(
            frm, text="Salvar senha", variable=self.save_var,
            font=FONT_SMALL, bg=BG, fg=FG, selectcolor=SURFACE,
            activebackground=BG, activeforeground=ACCENT,
        )
        self.chk_save.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        creds = _load_creds()
        if creds.get("host"):
            self.entry_host.delete(0, tk.END)
            self.entry_host.insert(0, creds["host"])
            self.entry_port.delete(0, tk.END)
            self.entry_port.insert(0, str(creds.get("port", 22)))
            self.entry_user.delete(0, tk.END)
            self.entry_user.insert(0, creds.get("user", "root"))
            self.entry_pass.delete(0, tk.END)
            self.entry_pass.insert(0, creds.get("password", ""))
            self.save_var.set(True)

        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.grid(row=2, column=0, pady=(12, pad), padx=pad, sticky="ew")
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)

        self.btn_conectar = _style_btn(btn_frm, "Conectar", self._on_connect, GREEN)
        self.btn_conectar.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_cancelar = _style_btn(btn_frm, "Cancelar", self.destroy, RED)
        self.btn_cancelar.grid(row=0, column=1, padx=4, sticky="ew")

        self.lbl_status = _style_label(self, "", FONT_SMALL, YELLOW)
        self.lbl_status.grid(row=3, column=0, padx=pad, pady=(0, pad), sticky="w")

        self.entry_host.focus_set()
        self.bind("<Return>", lambda e: self._on_connect())

    def _set_loading(self, loading):
        self.running = loading
        state = tk.DISABLED if loading else tk.NORMAL
        self.btn_conectar.configure(state=state)
        self.btn_cancelar.configure(state=state)
        self.btn_buscar.configure(state=state)
        self.lbl_status.configure(text="Conectando..." if loading else "")
        self.update()

    def _on_scan(self):
        if self.running:
            return
        self.lbl_status.configure(text="Buscando servidores OMV na rede...")
        self.btn_buscar.configure(state=tk.DISABLED)
        self.update()

        def scan():
            servers = []
            try:
                from zeroconf import Zeroconf, ServiceBrowser

                class ScanListener:
                    def __init__(self):
                        self.found = []

                    def add_service(self, zc, type_, name):
                        info = zc.get_service_info(type_, name)
                        if info and info.addresses:
                            host = info.server.rstrip(".") if info.server else ""
                            ip = socket.inet_ntoa(info.addresses[0])
                            port = info.port or 22
                            self.found.append((host, ip, port))

                    def update_service(self, zc, type_, name):
                        pass

                    def remove_service(self, zc, type_, name):
                        pass

                zc = Zeroconf()
                listener = ScanListener()
                ServiceBrowser(zc, "_ssh._tcp.local.", listener)
                ServiceBrowser(zc, "_workstation._tcp.local.", listener)
                time.sleep(4)
                zc.close()

                seen = set()
                for host, ip, port in listener.found:
                    key = (ip, port)
                    if key not in seen:
                        seen.add(key)
                        servers.append((host, ip, port))
            except Exception as e:
                self.after(0, lambda: self.lbl_status.configure(
                    text=f"Erro na busca: {e}"
                ))
                self.after(0, lambda: self.btn_buscar.configure(state=tk.NORMAL))
                return

            self.after(0, lambda: self._show_scan_results(servers))

        t = threading.Thread(target=scan, daemon=True)
        t.start()

    def _show_scan_results(self, servers):
        self.btn_buscar.configure(state=tk.NORMAL)
        if not servers:
            self.lbl_status.configure(text="Nenhum servidor encontrado na rede.")
            return

        if len(servers) == 1:
            host, ip, port = servers[0]
            self.entry_host.delete(0, tk.END)
            self.entry_host.insert(0, ip)
            self.entry_port.delete(0, tk.END)
            self.entry_port.insert(0, str(port))
            self.lbl_status.configure(text=f"Servidor encontrado: {host or ip}")
            return

        win = tk.Toplevel(self)
        win.title("Servidores encontrados")
        win.configure(bg=BG)
        win.transient(self)
        win.grab_set()
        win.geometry("550x300+{}+{}".format(
            self.winfo_rootx() + 40, self.winfo_rooty() + 60
        ))

        _style_label(win, "Selecione um servidor:", FONT_BOLD, CYAN) \
            .pack(padx=12, pady=(12, 4), anchor="w")

        cols = ("#", "Hostname", "IP", "Porta")
        tree = ttk.Treeview(win, columns=cols, show="headings",
                            height=8, selectmode="browse")
        tree.heading("#", text="#")
        tree.heading("Hostname", text="Hostname")
        tree.heading("IP", text="IP")
        tree.heading("Porta", text="Porta")
        tree.column("#", width=40, anchor="center")
        tree.column("Hostname", width=200)
        tree.column("IP", width=150)
        tree.column("Porta", width=80, anchor="center")

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=SURFACE, foreground=FG,
                        fieldbackground=SURFACE, font=FONT)
        style.configure("Treeview.Heading", background=BTN_BG, foreground=FG,
                        font=FONT_BOLD)
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", BG)])

        for i, (host, ip, port) in enumerate(servers, 1):
            tree.insert("", tk.END, values=(i, host or "-", ip, port))

        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 8))

        btn_frm = tk.Frame(win, bg=BG)
        btn_frm.pack(fill=tk.X, padx=12, pady=(0, 12))

        def selecionar():
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], "values")
            if not vals:
                return
            ip, port = vals[2], vals[3]
            self.entry_host.delete(0, tk.END)
            self.entry_host.insert(0, ip)
            self.entry_port.delete(0, tk.END)
            self.entry_port.insert(0, str(port))
            self.lbl_status.configure(text=f"Servidor selecionado: {vals[1] or ip}")
            win.destroy()

        tree.bind("<Double-Button-1>", lambda e: selecionar())

        _style_btn(btn_frm, "Selecionar", selecionar, GREEN) \
            .pack(side=tk.RIGHT, padx=4)
        _style_btn(btn_frm, "Cancelar", win.destroy, RED) \
            .pack(side=tk.RIGHT, padx=4)

    def _on_connect(self):
        if self.running:
            return
        host = self.entry_host.get().strip()
        port_str = self.entry_port.get().strip()
        user = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()
        save_pass = self.save_var.get()
        if not host or not user:
            self.lbl_status.configure(text="Preencha Host e Usuário.")
            return
        try:
            port = int(port_str) if port_str else 22
        except ValueError:
            self.lbl_status.configure(text="Porta inválida.")
            return
        self._set_loading(True)

        def task():
            try:
                cfg = ServerConfig(host=host, port=port, username=user, password=password)
                ssh = SSHClient(cfg, dry_run=False)
                ssh.connect()
                if save_pass:
                    _save_creds(host, port, user, password)
                else:
                    _clear_creds()
                self.result = ssh
                self.after(0, self.destroy)
            except Exception as e:
                self.after(0, lambda: self.lbl_status.configure(text=f"Erro: {e}"))
                self.after(0, lambda: self._set_loading(False))

        t = threading.Thread(target=task, daemon=True)
        t.start()


class UserDialog(tk.Toplevel):
    def __init__(self, parent, ssh, groups=None, user_data=None):
        super().__init__(parent)
        self.ssh = ssh
        self.groups = groups or []
        self.user_data = user_data
        self.result = None
        self.running = False
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.minsize(400, 380)
        self.geometry("440x460+{}+{}".format(
            parent.winfo_rootx() + 100, parent.winfo_rooty() + 60
        ))

    def _build(self):
        pad = 12
        is_edit = self.user_data is not None
        title = "Editar Usuário" if is_edit else "Adicionar Usuário"
        self.columnconfigure(0, weight=1)

        _style_label(self, title, FONT_TITLE, ACCENT) \
            .grid(row=0, column=0, pady=(pad, 4), padx=pad, sticky="w")

        frm = tk.Frame(self, bg=BG)
        frm.grid(row=1, column=0, sticky="ew", padx=pad, pady=4)
        frm.columnconfigure(1, weight=1)

        r = 0
        _style_label(frm, "Usuário:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_user = _style_entry(frm)
        self.entry_user.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit:
            self.entry_user.insert(0, self.user_data["username"])
            self.entry_user.configure(state="readonly", readonlybackground=SURFACE, fg=CYAN)
        r += 1

        _style_label(frm, "Senha:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_pass = _style_entry(frm, width=28)
        self.entry_pass.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit:
            self.entry_pass.configure(show="*")
            self._load_pass_status()
        r += 1

        _style_label(frm, "Email:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_email = _style_entry(frm)
        self.entry_email.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit and self.user_data.get("gecos"):
            self.entry_email.insert(0, self.user_data["gecos"])
        r += 1

        _style_label(frm, "Grupos:", FONT_BOLD, CYAN).grid(row=r, column=0, sticky="nw", pady=(8, 2))
        r += 1

        g_frm = tk.Frame(frm, bg=BG)
        g_frm.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        g_frm.columnconfigure(0, weight=1)
        g_frm.rowconfigure(0, weight=1)

        self.list_groups = tk.Listbox(
            g_frm, font=FONT, bg=SURFACE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief=tk.FLAT, bd=0, highlightthickness=0,
            selectmode=tk.MULTIPLE, height=6,
        )
        self.list_groups.grid(row=0, column=0, sticky="nsew")
        scroll = tk.Scrollbar(g_frm, command=self.list_groups.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list_groups.configure(yscrollcommand=scroll.set)

        for g in sorted(self.groups):
            self.list_groups.insert(tk.END, g)

        if is_edit:
            selected = []
            pg = self.user_data.get("primary_group", "")
            if pg:
                selected.append(pg)
            extra = self.user_data.get("groups", "")
            if extra:
                selected.extend(g.strip() for g in extra.split(",") if g.strip())
            for i in range(self.list_groups.size()):
                if self.list_groups.get(i) in selected:
                    self.list_groups.select_set(i)

        _style_label(
            frm, "O primeiro grupo selecionado será o primário",
            FONT_SMALL, FG,
        ).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        r += 2

        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.grid(row=2, column=0, pady=(12, pad), padx=pad, sticky="ew")
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)

        label = "Salvar" if is_edit else "Criar"
        self.btn_ok = _style_btn(btn_frm, label, self._on_ok, GREEN)
        self.btn_ok.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_cancel = _style_btn(btn_frm, "Cancelar", self.destroy, RED)
        self.btn_cancel.grid(row=0, column=1, padx=4, sticky="ew")

        self.lbl_status = _style_label(self, "", FONT_SMALL, YELLOW)
        self.lbl_status.grid(row=3, column=0, padx=pad, pady=(0, pad), sticky="w")

    def _set_loading(self, loading):
        self.running = loading
        state = tk.DISABLED if loading else tk.NORMAL
        self.btn_ok.configure(state=state)
        self.btn_cancel.configure(state=state)

    def _load_pass_status(self):
        username = self.user_data["username"]

        def task():
            try:
                _, out, _ = self.ssh.exec(f"passwd -S '{username}' 2>/dev/null", timeout=10)
                status = out.strip()
                if status:
                    parts = status.split()
                    if len(parts) >= 2 and parts[1] == "P":
                        date = parts[2] if len(parts) > 2 else "?"
                        self.after(0, lambda: self.lbl_status.configure(
                            text=f"Senha definida (última alteração: {date})", fg=GREEN
                        ))
                    elif len(parts) >= 2 and parts[1] == "L":
                        self.after(0, lambda: self.lbl_status.configure(
                            text="Senha bloqueada!", fg=RED
                        ))
                    else:
                        self.after(0, lambda: self.lbl_status.configure(
                            text="Sem senha definida", fg=YELLOW
                        ))
                else:
                    self.after(0, lambda: self.lbl_status.configure(
                        text="Não foi possível verificar senha", fg=YELLOW
                    ))
            except Exception:
                pass

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_ok(self):
        if self.running:
            return
        username = self.entry_user.get().strip()
        if not username:
            self.lbl_status.configure(text="Nome de usuário é obrigatório.")
            return
        password = self.entry_pass.get().strip()
        email = self.entry_email.get().strip()
        is_edit = self.user_data is not None
        if not is_edit and not password:
            self.lbl_status.configure(text="Senha é obrigatória para novo usuário.")
            return
        sel = self.list_groups.curselection()
        selected = [self.list_groups.get(i) for i in sel]
        primary_group = selected[0] if selected else ""
        extra_groups = ",".join(selected[1:]) if len(selected) > 1 else ""
        if not primary_group:
            self.lbl_status.configure(text="Selecione ao menos um grupo.")
            return
        self.result = {
            "username": username, "password": password,
            "primary_group": primary_group, "extra_groups": extra_groups,
            "email": email, "edit_mode": is_edit,
        }
        self._set_loading(True)
        self.after(200, self.destroy)


class FolderDialog(tk.Toplevel):
    def __init__(self, parent, ssh, groups=None, disks=None, folder_data=None):
        super().__init__(parent)
        self.ssh = ssh
        self.groups = groups or []
        self.disks = disks or []
        self.folder_data = folder_data
        self.result = None
        self.running = False
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.minsize(520, 380)
        self.geometry("580x420+{}+{}".format(
            parent.winfo_rootx() + 100, parent.winfo_rooty() + 60
        ))

    def _build(self):
        pad = 12
        is_edit = self.folder_data is not None
        title = "Editar Pasta" if is_edit else "Adicionar Pasta"
        self.columnconfigure(0, weight=1)

        _style_label(self, title, FONT_TITLE, ACCENT) \
            .grid(row=0, column=0, pady=(pad, 4), padx=pad, sticky="w")

        frm = tk.Frame(self, bg=BG)
        frm.grid(row=1, column=0, sticky="ew", padx=pad, pady=4)
        frm.columnconfigure(1, weight=1)

        r = 0
        _style_label(frm, "Nome:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_name = _style_entry(frm)
        self.entry_name.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit:
            self.entry_name.insert(0, self.folder_data["name"])
            self.entry_name.configure(state="readonly", readonlybackground=SURFACE, fg=CYAN)
        r += 1

        _style_label(frm, "Disco:").grid(row=r, column=0, sticky="w", pady=3)
        disk_vals = []
        disk_map = {}
        for d in self.disks:
            label = f"{d['dev']} ({d['fstype']})  {d['size']}  disp:{d['avail']}"
            disk_vals.append(label)
            disk_map[label] = d
        self.disk_map = disk_map
        self.cb_disk = ttk.Combobox(
            frm, values=disk_vals, font=FONT, width=34, state="readonly",
        )
        self.cb_disk.set("")
        self.cb_disk.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if disk_vals:
            self.cb_disk.current(0)
        if is_edit and self.folder_data.get("disk"):
            for lbl, d in disk_map.items():
                if d["mount"] == self.folder_data["disk"]:
                    self.cb_disk.set(lbl)
                    break
        r += 1

        _style_label(frm, "Grupo:").grid(row=r, column=0, sticky="w", pady=3)
        self.cb_group = ttk.Combobox(
            frm, values=sorted(self.groups), font=FONT, width=28, state="readonly",
        )
        self.cb_group.set("")
        self.cb_group.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit and self.folder_data.get("group"):
            self.cb_group.set(self.folder_data["group"])
        elif not is_edit:
            self.cb_group.set("users")
        r += 1

        _style_label(frm, "Permissão:").grid(row=r, column=0, sticky="nw", pady=3)
        self.perm_var = tk.StringVar()
        current_perm = self.folder_data.get("permissions", "770") if is_edit else "770"
        in_presets = any(current_perm == v for v, _ in _PERM_PRESETS)
        perm_frm = tk.Frame(frm, bg=BG)
        perm_frm.grid(row=r, column=1, sticky="w", pady=3, padx=(8, 0))
        for val, desc in _PERM_PRESETS:
            rb = tk.Radiobutton(
                perm_frm, text=f"{val} — {desc}", variable=self.perm_var,
                value=val, font=FONT_SMALL, bg=BG, fg=FG,
                selectcolor=SURFACE, activebackground=BG, activeforeground=ACCENT,
                anchor="w", justify=tk.LEFT, tristatevalue="",
            )
            rb.pack(fill="x", pady=1)
        self.perm_var.set(current_perm if in_presets else "")
        # Custom entry
        cust_frm = tk.Frame(perm_frm, bg=BG)
        cust_frm.pack(fill="x", pady=1)
        self.perm_custom_rb = tk.Radiobutton(
            cust_frm, text="Personalizado:", variable=self.perm_var,
            value="", font=FONT_SMALL, bg=BG, fg=FG,
            selectcolor=SURFACE, activebackground=BG, activeforeground=ACCENT,
        )
        self.perm_custom_rb.pack(side=tk.LEFT)
        self.entry_perm = _style_entry(cust_frm, width=8)
        self.entry_perm.pack(side=tk.LEFT, padx=(4, 0))
        if not in_presets and is_edit:
            self.entry_perm.insert(0, current_perm)
            self.perm_var.set("")
        r += 1

        self.smb_var = tk.BooleanVar(value=True)
        self.chk_smb = tk.Checkbutton(
            frm, text="Criar compartilhamento SMB", variable=self.smb_var,
            font=FONT_SMALL, bg=BG, fg=FG, selectcolor=SURFACE,
            activebackground=BG, activeforeground=ACCENT,
        )
        self.chk_smb.grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 0))

        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.grid(row=2, column=0, pady=(12, pad), padx=pad, sticky="ew")
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)

        label = "Salvar" if is_edit else "Criar"
        self.btn_ok = _style_btn(btn_frm, label, self._on_ok, GREEN)
        self.btn_ok.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_cancel = _style_btn(btn_frm, "Cancelar", self.destroy, RED)
        self.btn_cancel.grid(row=0, column=1, padx=4, sticky="ew")

        self.lbl_status = _style_label(self, "", FONT_SMALL, YELLOW)
        self.lbl_status.grid(row=3, column=0, padx=pad, pady=(0, pad), sticky="w")

    def _set_loading(self, loading):
        self.running = loading
        state = tk.DISABLED if loading else tk.NORMAL
        self.btn_ok.configure(state=state)
        self.btn_cancel.configure(state=state)

    def _on_ok(self):
        if self.running:
            return
        name = self.entry_name.get().strip()
        if not name:
            self.lbl_status.configure(text="Nome da pasta é obrigatório.")
            return
        perm = self.perm_var.get().strip() or self.entry_perm.get().strip()
        if not re.match(r"^\d{3,4}$", perm):
            self.lbl_status.configure(text="Permissão deve ser 3-4 dígitos (ex: 770).")
            return
        disk_label = self.cb_disk.get()
        disk = self.disk_map.get(disk_label, {})
        if not disk.get("mount"):
            self.lbl_status.configure(text="Selecione um disco.")
            return
        group = self.cb_group.get().strip() or "users"
        self.result = {
            "name": name, "group": group, "permissions": perm,
            "disk_uuid": disk.get("uuid", ""),
            "disk_mount": disk.get("mount", ""),
            "disk_dev": disk.get("dev", ""),
            "create_smb": self.smb_var.get(),
            "edit_mode": self.folder_data is not None,
        }
        self._set_loading(True)
        self.after(200, self.destroy)


class GroupDialog(tk.Toplevel):
    def __init__(self, parent, ssh, users=None, group_data=None):
        super().__init__(parent)
        self.ssh = ssh
        self.users = users or []
        self.group_data = group_data
        self.result = None
        self.running = False
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.minsize(400, 380)
        self.geometry("440x460+{}+{}".format(
            parent.winfo_rootx() + 100, parent.winfo_rooty() + 60
        ))

    def _build(self):
        pad = 12
        is_edit = self.group_data is not None
        title = "Editar Grupo" if is_edit else "Adicionar Grupo"
        self.columnconfigure(0, weight=1)

        _style_label(self, title, FONT_TITLE, ACCENT) \
            .grid(row=0, column=0, pady=(pad, 4), padx=pad, sticky="w")

        frm = tk.Frame(self, bg=BG)
        frm.grid(row=1, column=0, sticky="ew", padx=pad, pady=4)
        frm.columnconfigure(1, weight=1)

        r = 0
        _style_label(frm, "Nome:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_name = _style_entry(frm)
        self.entry_name.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit:
            self.entry_name.insert(0, self.group_data["name"])
            self.entry_name.configure(state="readonly", readonlybackground=SURFACE, fg=CYAN)
        r += 1

        _style_label(frm, "Comentário:").grid(row=r, column=0, sticky="w", pady=3)
        self.entry_comment = _style_entry(frm)
        self.entry_comment.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if is_edit and self.group_data.get("comment"):
            self.entry_comment.insert(0, self.group_data["comment"])
        r += 1

        _style_label(frm, "Membros:", FONT_BOLD, CYAN).grid(row=r, column=0, sticky="nw", pady=(8, 2))
        r += 1

        m_frm = tk.Frame(frm, bg=BG)
        m_frm.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        m_frm.columnconfigure(0, weight=1)
        m_frm.rowconfigure(0, weight=1)

        self.list_members = tk.Listbox(
            m_frm, font=FONT, bg=SURFACE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief=tk.FLAT, bd=0, highlightthickness=0,
            selectmode=tk.MULTIPLE, height=6,
        )
        self.list_members.grid(row=0, column=0, sticky="nsew")
        scroll = tk.Scrollbar(m_frm, command=self.list_members.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list_members.configure(yscrollcommand=scroll.set)

        for u in sorted(self.users, key=lambda x: x["username"]):
            self.list_members.insert(tk.END, u["username"])

        if is_edit:
            members = self.group_data.get("members", [])
            for i in range(self.list_members.size()):
                if self.list_members.get(i) in members:
                    self.list_members.select_set(i)

        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.grid(row=2, column=0, pady=(12, pad), padx=pad, sticky="ew")
        btn_frm.columnconfigure(0, weight=1)
        btn_frm.columnconfigure(1, weight=1)

        label = "Salvar" if is_edit else "Criar"
        self.btn_ok = _style_btn(btn_frm, label, self._on_ok, GREEN)
        self.btn_ok.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_cancel = _style_btn(btn_frm, "Cancelar", self.destroy, RED)
        self.btn_cancel.grid(row=0, column=1, padx=4, sticky="ew")

        self.lbl_status = _style_label(self, "", FONT_SMALL, YELLOW)
        self.lbl_status.grid(row=3, column=0, padx=pad, pady=(0, pad), sticky="w")

    def _set_loading(self, loading):
        self.running = loading
        state = tk.DISABLED if loading else tk.NORMAL
        self.btn_ok.configure(state=state)
        self.btn_cancel.configure(state=state)

    def _on_ok(self):
        if self.running:
            return
        name = self.entry_name.get().strip()
        if not name:
            self.lbl_status.configure(text="Nome do grupo é obrigatório.")
            return
        comment = self.entry_comment.get().strip()
        sel = self.list_members.curselection()
        members = [self.list_members.get(i) for i in sel]
        self.result = {
            "name": name, "comment": comment, "members": members,
            "edit_mode": self.group_data is not None,
        }
        self._set_loading(True)
        self.after(200, self.destroy)


# ===========================================================================
# MAIN GUI
# ===========================================================================

class OMVGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OMV Manager")
        self.root.geometry("1100x720")
        self.root.configure(bg=BG)
        self.root.minsize(800, 500)
        _icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "omv.ico")
        if os.path.exists(_icon):
            try:
                self.root.iconbitmap(default=_icon)
            except Exception:
                pass

        self.ssh = None
        self.base_path = ""
        self.connected = False
        self.users_data = []
        self.folders_data = []
        self.groups_data = []
        self.groups_list = []
        self.disks_list = []
        self.log_queue = queue.Queue()
        self.busy = False

        self._build_ui()
        self._poll_queue()
        self._update_ui_state()

    # ===================================================================
    # BUILD UI
    # ===================================================================

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)
        self.root.rowconfigure(2, weight=1)
        self.root.rowconfigure(3, weight=0)

        self._build_menu()
        self._build_topbar()
        self._build_info_bar()
        self._build_notebook()
        self._build_log()
        self.root.bind("<Control-e>", lambda e: self._on_export())
        self.root.bind("<Control-E>", lambda e: self._on_export())
        self.root.bind("<Control-i>", lambda e: self._on_import())
        self.root.bind("<Control-I>", lambda e: self._on_import())

    def _build_menu(self):
        menubar = tk.Menu(self.root, bg=BTN_BG, fg=FG,
                          activebackground=ACCENT, activeforeground=BG,
                          font=FONT)

        self.arquivo_menu = tk.Menu(menubar, tearoff=False, bg=SURFACE, fg=FG,
                                    activebackground=ACCENT, activeforeground=BG,
                                    font=FONT)
        self.arquivo_menu.add_command(label="Exportar configuração...",
                                      command=self._on_export,
                                      accelerator="Ctrl+E", state=tk.DISABLED)
        self.arquivo_menu.add_command(label="Importar configuração...",
                                      command=self._on_import,
                                      accelerator="Ctrl+I", state=tk.DISABLED)
        menubar.add_cascade(label="Arquivo", menu=self.arquivo_menu)

        ajuda = tk.Menu(menubar, tearoff=False, bg=SURFACE, fg=FG,
                        activebackground=ACCENT, activeforeground=BG,
                        font=FONT)
        ajuda.add_command(label="Instruções", command=self._on_ajuda_instrucoes)
        ajuda.add_separator()
        ajuda.add_command(label="Sobre", command=self._on_ajuda_sobre)
        menubar.add_cascade(label="Ajuda", menu=ajuda)
        self.root.config(menu=menubar)

    def _on_ajuda_instrucoes(self):
        msg = (
            "OMV Manager — Instruções de Uso\n\n"
            "1. Conecte a um servidor OMV usando o botão Conectar.\n"
            "   Informe IP, porta (22), usuário (root) e senha.\n"
            "   Use o botão Buscar para descobrir servidores na rede.\n\n"
            "2. Aba Usuários:\n"
            "   - Adicionar: cria usuário no Linux + OMV + Samba\n"
            "   - Editar: altera grupos, senha e email\n"
            "   - Apagar: remove do sistema (com confirmação)\n\n"
            "3. Aba Pastas:\n"
            "   - Adicionar: cria pasta no disco selecionado,\n"
            "     registra no OMV como shared folder e\n"
            "     opcionalmente cria compartilhamento SMB\n"
            "   - Editar: altera proprietário e permissões\n"
            "   - Apagar: remove pasta, shared folder e SMB share\n\n"
            "4. Aba Grupos:\n"
            "   - Gerencia grupos no Linux e no OMV\n"
            "   - Adiciona/remove membros por multisseleção\n\n"
            "5. Arquivo > Exportar configuração:\n"
            "   - Salva backup JSON de usuários, grupos e pastas\n"
            "6. Arquivo > Importar configuração:\n"
            "   - Restaura grupos, usuários (sem senha) e pastas\n\n"
            "Todas as operações são aplicadas via SSH no servidor OMV."
        )
        messagebox.showinfo("Instruções", msg, parent=self.root)

    def _on_ajuda_sobre(self):
        msg = (
            "OMV Manager v0.6b\n\n"
            "Gerenciamento automatizado de usuários, grupos,\n"
            "pastas, ACLs e compartilhamentos SMB no\n"
            "OpenMediaVault 8.x (Debian 13)\n\n"
            "Desenvolvido por: Autolinx Automação\n"
            "https://autolinx.me\n\n"
            "OpenMediaVault é mantido pela CodeSphere.\n"
            "© 2026 Autolinx Automação. Todos os direitos reservados."
        )
        messagebox.showinfo("Sobre", msg, parent=self.root)

    def _build_topbar(self):
        top = tk.Frame(self.root, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        top.columnconfigure(9, weight=1)

        self.btn_connect = _style_btn(top, "Conectar", self._on_connect, ACCENT)
        self.btn_connect.grid(row=0, column=0, padx=(0, 8))

        ttk.Separator(top, orient="vertical").grid(row=0, column=1, padx=4, sticky="ns")

        self.lbl_conn = _style_label(top, "Desconectado", FONT_SMALL, RED)
        self.lbl_conn.grid(row=0, column=2, padx=8)

        self.lbl_host = _style_label(top, "", FONT_SMALL, FG)
        self.lbl_host.grid(row=0, column=3, padx=4)

    def _build_info_bar(self):
        self.info_frm = tk.Frame(self.root, bg=SURFACE, bd=0, highlightthickness=0)
        self.info_frm.grid(row=1, column=0, sticky="ew", padx=8, pady=(6, 0))
        self.info_frm.columnconfigure(tuple(range(4)), weight=1)

        self.lbl_hostname = _style_label(self.info_frm, "", FONT_SMALL, CYAN)
        self.lbl_hostname.grid(row=0, column=0, padx=8, pady=4, sticky="w")

        self.lbl_os = _style_label(self.info_frm, "", FONT_SMALL, FG)
        self.lbl_os.grid(row=0, column=1, padx=8, pady=4, sticky="w")

        self.lbl_uptime = _style_label(self.info_frm, "", FONT_SMALL, FG)
        self.lbl_uptime.grid(row=0, column=2, padx=8, pady=4, sticky="w")

        self.lbl_storage = _style_label(self.info_frm, "", FONT_SMALL, YELLOW)
        self.lbl_storage.grid(row=0, column=3, padx=8, pady=4, sticky="w")

        self.info_frm.grid_remove()

    def _build_notebook(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BTN_BG, foreground=FG,
                        padding=[16, 4], font=FONT_BOLD)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", BG)])

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(6, 0))
        self.root.rowconfigure(2, weight=1)

        self._build_user_tab()
        self._build_folder_tab()
        self._build_group_tab()

    def _build_user_tab(self):
        frm = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frm, text="  Usuários  ")

        btn_frm = tk.Frame(frm, bg=BG)
        btn_frm.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.btn_user_add = _style_btn(btn_frm, "+ Adicionar", self._on_user_add, GREEN)
        self.btn_user_add.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_user_edit = _style_btn(btn_frm, "Editar", self._on_user_edit, CYAN)
        self.btn_user_edit.pack(side=tk.LEFT, padx=4)

        self.btn_user_del = _style_btn(btn_frm, "Apagar", self._on_user_delete, RED)
        self.btn_user_del.pack(side=tk.LEFT, padx=4)

        list_frm = tk.Frame(frm, bg=BG)
        list_frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        list_frm.columnconfigure(0, weight=1)
        list_frm.rowconfigure(1, weight=1)

        _style_label(
            list_frm,
            f"{'Usuário':20}  {'Grupo Primário':15}  Grupos",
            FONT_SMALL, CYAN,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4)

        self.list_users = tk.Listbox(
            list_frm, font=FONT, bg=SURFACE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief=tk.FLAT, bd=0, highlightthickness=0,
        )
        self.list_users.grid(row=1, column=0, sticky="nsew")
        self.list_users.bind("<Double-Button-1>", lambda e: self._on_user_edit())

        scroll = tk.Scrollbar(list_frm, command=self.list_users.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.list_users.configure(yscrollcommand=scroll.set)

    def _build_folder_tab(self):
        frm = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frm, text="  Pastas  ")

        btn_frm = tk.Frame(frm, bg=BG)
        btn_frm.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.btn_folder_add = _style_btn(btn_frm, "+ Adicionar", self._on_folder_add, GREEN)
        self.btn_folder_add.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_folder_edit = _style_btn(btn_frm, "Editar", self._on_folder_edit, CYAN)
        self.btn_folder_edit.pack(side=tk.LEFT, padx=4)

        self.btn_folder_del = _style_btn(btn_frm, "Apagar", self._on_folder_delete, RED)
        self.btn_folder_del.pack(side=tk.LEFT, padx=4)

        list_frm = tk.Frame(frm, bg=BG)
        list_frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        list_frm.columnconfigure(0, weight=1)
        list_frm.rowconfigure(1, weight=1)

        _style_label(
            list_frm,
            f"{'Pasta':20}  {'Disco':22}  {'Perm':>4}  Comentário",
            FONT_SMALL, CYAN,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4)

        self.list_folders = tk.Listbox(
            list_frm, font=FONT, bg=SURFACE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief=tk.FLAT, bd=0, highlightthickness=0,
        )
        self.list_folders.grid(row=1, column=0, sticky="nsew")
        self.list_folders.bind("<Double-Button-1>", lambda e: self._on_folder_edit())

        scroll = tk.Scrollbar(list_frm, command=self.list_folders.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.list_folders.configure(yscrollcommand=scroll.set)

    def _build_group_tab(self):
        frm = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frm, text="  Grupos  ")

        btn_frm = tk.Frame(frm, bg=BG)
        btn_frm.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.btn_group_add = _style_btn(btn_frm, "+ Adicionar", self._on_group_add, GREEN)
        self.btn_group_add.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_group_edit = _style_btn(btn_frm, "Editar", self._on_group_edit, CYAN)
        self.btn_group_edit.pack(side=tk.LEFT, padx=4)

        self.btn_group_del = _style_btn(btn_frm, "Apagar", self._on_group_delete, RED)
        self.btn_group_del.pack(side=tk.LEFT, padx=4)

        list_frm = tk.Frame(frm, bg=BG)
        list_frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        list_frm.columnconfigure(0, weight=1)
        list_frm.rowconfigure(1, weight=1)

        _style_label(
            list_frm,
            f"{'Grupo':20}  {'Comentário':30}   Membros",
            FONT_SMALL, CYAN,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4)

        self.list_groups_tab = tk.Listbox(
            list_frm, font=FONT, bg=SURFACE, fg=FG, selectbackground=ACCENT,
            selectforeground=BG, relief=tk.FLAT, bd=0, highlightthickness=0,
        )
        self.list_groups_tab.grid(row=1, column=0, sticky="nsew")
        self.list_groups_tab.bind("<Double-Button-1>", lambda e: self._on_group_edit())

        scroll = tk.Scrollbar(list_frm, command=self.list_groups_tab.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.list_groups_tab.configure(yscrollcommand=scroll.set)

    def _build_log(self):
        log_frm = tk.Frame(self.root, bg=BG)
        log_frm.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 8))
        log_frm.columnconfigure(0, weight=1)

        _style_label(log_frm, "Log:", FONT_SMALL, FG).pack(anchor="sw")

        text_frm = tk.Frame(log_frm, bg=BG)
        text_frm.pack(fill=tk.X, expand=True)

        self.log_text = tk.Text(
            text_frm, font=FONT_SMALL, bg=SURFACE, fg=FG,
            insertbackground=FG, relief=tk.FLAT, bd=0, height=6,
            wrap=tk.NONE, state=tk.DISABLED,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = tk.Scrollbar(text_frm, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=log_scroll.set)

    # ===================================================================
    # UI STATE
    # ===================================================================

    def _update_ui_state(self):
        state = tk.NORMAL if self.connected and not self.busy else tk.DISABLED
        for btn in (self.btn_user_add, self.btn_user_edit, self.btn_user_del,
                    self.btn_folder_add, self.btn_folder_edit, self.btn_folder_del,
                    self.btn_group_add, self.btn_group_edit, self.btn_group_del):
            btn.configure(state=state)
        self.arquivo_menu.entryconfig("Exportar configuração...", state=state)
        self.arquivo_menu.entryconfig("Importar configuração...", state=state)
        if not self.connected:
            self.list_users.delete(0, tk.END)
            self.list_folders.delete(0, tk.END)
            self.list_groups_tab.delete(0, tk.END)
            self.users_data = []
            self.folders_data = []
            self.groups_data = []
            self.info_frm.grid_remove()

    def _set_busy(self, busy):
        self.busy = busy
        self._update_ui_state()

    # ===================================================================
    # LOG
    # ===================================================================

    def _log(self, msg, color=FG):
        self.log_text.configure(state=tk.NORMAL)
        tag = f"c{id(color)}"
        self.log_text.tag_config(tag, foreground=color)
        self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_queue(self):
        while not self.log_queue.empty():
            item = self.log_queue.get_nowait()
            if isinstance(item, tuple):
                kind, payload = item
                if kind == "log":
                    self._log(payload[0], payload[1])
                elif kind == "users_loaded":
                    self._on_users_loaded(payload)
                elif kind == "folders_loaded":
                    self._on_folders_loaded(payload)
                elif kind == "disks_loaded":
                    self._on_disks_loaded(payload)
                elif kind == "groups_list_loaded":
                    self.groups_list = payload
                elif kind == "groups_full_loaded":
                    self._on_groups_loaded(payload)
                elif kind == "info_loaded":
                    self._on_info_loaded(payload)
                elif kind == "error":
                    self._log(f"[ERRO] {payload}", RED)
                    self._set_busy(False)
                elif kind == "done":
                    self._set_busy(False)
            else:
                self._log(str(item))
        self.root.after(100, self._poll_queue)

    # ===================================================================
    # CONNECT
    # ===================================================================

    def _on_connect(self):
        if self.busy:
            return
        dlg = ConnectionDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.result:
            self.ssh = dlg.result
            self.connected = True
            self.lbl_conn.configure(text="Conectado", fg=GREEN)
            self.lbl_host.configure(text=f"{self.ssh.config.host}:{self.ssh.config.port}")
            self._log(f"Conectado a {self.ssh.config.host}:{self.ssh.config.port}", GREEN)
            self.base_path = _ssh_detect_base_path(self.ssh)
            self._log(f"Caminho base detectado: {self.base_path}", CYAN)
            self._load_server_data()
            self._update_ui_state()

    # ===================================================================
    # LOAD SERVER DATA
    # ===================================================================

    def _load_server_data(self):
        self._set_busy(True)
        self._log("Carregando dados do servidor...", CYAN)

        def task():
            try:
                self._ssh_load_info()
                self._ssh_load_groups_list()
                self._ssh_load_users()
                self._ssh_load_disks()
                self._ssh_load_folders()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _ssh_load_info(self):
        try:
            _, hostname, _ = self.ssh.exec("hostname", timeout=10)
            _, os_info, _ = self.ssh.exec(
                "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
                timeout=10,
            )
            _, uptime, _ = self.ssh.exec("uptime -p 2>/dev/null || uptime", timeout=10)
            _, storage, _ = self.ssh.exec(
                f"df -h '{self.base_path}' 2>/dev/null | tail -1 | awk '{{print $2, $3, $4, $5}}'",
                timeout=10,
            )
            self.log_queue.put(("info_loaded", {
                "hostname": hostname.strip(),
                "os": os_info.strip() or "Desconhecido",
                "uptime": uptime.strip(),
                "storage": storage.strip() or "N/D",
            }))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar info: {e}"))

    def _ssh_load_groups_list(self):
        try:
            _, out, _ = self.ssh.exec(
                """python3 -c "
import xml.etree.ElementTree as ET
t = ET.parse('/etc/openmediavault/config.xml')
for g in t.findall('.//system/usermanagement/groups/group/name'):
    print(g.text)
" """, timeout=15)
            groups = sorted(g.strip() for g in out.split("\n") if g.strip())
            self.log_queue.put(("groups_list_loaded", groups))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar grupos OMV: {e}"))

    def _ssh_load_groups_full(self):
        try:
            _, out, _ = self.ssh.exec(
                """python3 -c "
import xml.etree.ElementTree as ET
t = ET.parse('/etc/openmediavault/config.xml')
for g in t.findall('.//system/usermanagement/groups/group'):
    name = g.find('name')
    comment = g.find('comment')
    print((name.text or '') + '|' + (comment.text or ''))
" """, timeout=15)
            groups = []
            for line in out.strip().split("\n"):
                if not line or "|" not in line:
                    continue
                name, comment = line.split("|", 1)
                name = name.strip()
                if not name:
                    continue
                _, m_out, _ = self.ssh.exec(
                    f"getent group '{name}' 2>/dev/null | cut -d: -f4", timeout=5
                )
                members = [m.strip() for m in m_out.strip().split(",") if m.strip()]
                groups.append({
                    "name": name,
                    "comment": comment.strip(),
                    "members": members,
                })
            self.log_queue.put(("groups_full_loaded", groups))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar grupos: {e}"))

    def _ssh_load_users(self):
        try:
            _, out, _ = self.ssh.exec(
                "getent passwd | awk -F: '$3>=1000 && $3<65534 && $7 !~ /\\/(nologin|false)$/ {print}'",
                timeout=15,
            )
            users = []
            for line in out.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                username = parts[0]
                _, g_out, _ = self.ssh.exec(f"groups {username} 2>/dev/null", timeout=5)
                all_groups = g_out.replace(f"{username} : ", "").strip().split() if g_out else []
                _, pg_out, _ = self.ssh.exec(f"getent group {parts[3]} | cut -d: -f1", timeout=5)
                primary = pg_out.strip()
                extra_groups = [g for g in all_groups if g != primary]
                users.append({
                    "username": username, "uid": parts[2], "gid": parts[3],
                    "primary_group": primary, "gecos": parts[4],
                    "home": parts[5], "shell": parts[6],
                    "groups": ",".join(extra_groups),
                })
            self.log_queue.put(("users_loaded", users))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar usuários: {e}"))

    def _ssh_load_folders(self):
        try:
            _, out, _ = self.ssh.exec(
                """python3 -c "
import xml.etree.ElementTree as ET
t = ET.parse('/etc/openmediavault/config.xml')
# Build mount map
mount_map = {}
for m in t.findall('.//mntent'):
    muuid = (m.find('uuid').text or '').strip()
    mdir = (m.find('dir').text or '').strip()
    mtype = (m.find('type').text or '?').strip()
    if muuid and mdir:
        mount_map[muuid] = (mdir, mtype)
for sf in t.findall('.//sharedfolder'):
    name = (sf.find('name').text or '').strip()
    mref = (sf.find('mntentref').text or '').strip()
    rel = (sf.find('reldirpath').text or '').strip().rstrip('/')
    comment = (sf.find('comment').text or '').strip()
    mount, fstype = mount_map.get(mref, ('', ''))
    path = mount + '/' + rel if mount else rel
    print(name + '|' + path + '|' + mount + '|' + fstype + '|' + comment)
" """, timeout=15)
            folders = []
            for line in out.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 2:
                    continue
                name = parts[0]
                path = parts[1]
                disk = parts[2] if len(parts) > 2 else ""
                fstype = parts[3] if len(parts) > 3 else ""
                comment = parts[4] if len(parts) > 4 else ""
                _, stat_out, _ = self.ssh.exec(
                    f"stat -c '%U:%G:%A' '{path}' 2>/dev/null", timeout=5
                )
                sp = stat_out.strip().split(":")
                folders.append({
                    "name": name, "path": path, "disk": disk, "fstype": fstype,
                    "owner": sp[0] if len(sp) > 0 else "",
                    "group": sp[1] if len(sp) > 1 else "",
                    "permissions": _sym_to_octal(sp[2]) if len(sp) > 2 else "770",
                    "comment": comment,
                })
            self.log_queue.put(("folders_loaded", folders))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar pastas: {e}"))

    def _ssh_load_disks(self):
        try:
            _, out, _ = self.ssh.exec(
                """python3 -c "
import xml.etree.ElementTree as ET, subprocess, os
t = ET.parse('/etc/openmediavault/config.xml')
for m in t.findall('.//mntent'):
    uuid = (m.find('uuid').text or '').strip()
    fsname = (m.find('fsname').text or '').strip()
    mount = (m.find('dir').text or '').strip()
    fstype = (m.find('type').text or '').strip()
    try:
        real = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '-T', mount], stderr=subprocess.DEVNULL).decode().strip()
        if real and real != '/dev/root':
            fsname = real
    except: pass
    dev = fsname.rsplit('/', 1)[-1] if fsname else '?'
    size = used = avail = use_pct = '?'
    try:
        o = subprocess.check_output(['df', '-h', mount], stderr=subprocess.DEVNULL).decode()
        p = o.strip().split(chr(10))[1].split() if chr(10) in o else []
        if len(p) >= 5: size, used, avail, use_pct = p[1], p[2], p[3], p[4]
    except: pass
    print(uuid + '|' + dev + '|' + mount + '|' + fstype + '|' + size + '|' + avail + '|' + use_pct)
" """, timeout=15)
            disks = []
            for line in out.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                disks.append({
                    "uuid": parts[0], "dev": parts[1], "mount": parts[2],
                    "fstype": parts[3], "size": parts[4] if len(parts) > 4 else "?",
                    "avail": parts[5] if len(parts) > 5 else "?",
                    "use_pct": parts[6] if len(parts) > 6 else "?",
                })
            self.log_queue.put(("disks_loaded", disks))
        except Exception as e:
            self.log_queue.put(("error", f"Erro ao carregar discos: {e}"))

    # ===================================================================
    # CALLBACKS
    # ===================================================================

    def _on_info_loaded(self, info):
        self.info_frm.grid()
        self.lbl_hostname.configure(text=f"Servidor: {info['hostname']}")
        self.lbl_os.configure(text=f"Sistema: {info['os']}")
        self.lbl_uptime.configure(text=f"Ativo: {info['uptime']}")
        self.lbl_storage.configure(text=f"Disco: {info['storage']}")
        self._log(f"Servidor: {info['hostname']} | {info['os']}", CYAN)

    def _on_users_loaded(self, users):
        self.users_data = users
        self.list_users.delete(0, tk.END)
        for u in users:
            label = f"{u['username']:20}  {u['primary_group']:15}  [{u['groups'][:40]}]"
            self.list_users.insert(tk.END, label)
        self._log(f"{len(users)} usuários carregados", GREEN)

    def _on_folders_loaded(self, folders):
        self.folders_data = folders
        self.list_folders.delete(0, tk.END)
        disk_by_mount = {d["mount"]: d for d in self.disks_list}
        for f in folders:
            dmount = f.get("disk", "")
            d = disk_by_mount.get(dmount)
            if d:
                disk_label = f"{d['dev']} ({d['fstype']}) {d['size']}"
            else:
                disk_label = dmount.rsplit("/", 1)[-1] if dmount else "?"
            label = f"{f['name']:20}  {disk_label:22}  {f['permissions']:>4}  {f.get('comment','')[:25]}"
            self.list_folders.insert(tk.END, label)
        self._log(f"{len(folders)} pastas carregadas", GREEN)

    def _on_disks_loaded(self, disks):
        self.disks_list = disks
        self._log(f"{len(disks)} discos detectados", CYAN)

    def _on_groups_loaded(self, groups):
        self.groups_data = groups
        self.list_groups_tab.delete(0, tk.END)
        for g in groups:
            members_str = ", ".join(g["members"]) if g["members"] else "-"
            label = f"{g['name']:20}  {g['comment'][:30]:30}  [{members_str[:40]}]"
            self.list_groups_tab.insert(tk.END, label)
        self._log(f"{len(groups)} grupos carregados", GREEN)

    # ===================================================================
    # USER CRUD
    # ===================================================================

    def _get_selected_user(self):
        sel = self.list_users.curselection()
        if not sel:
            messagebox.showwarning("Seleção", "Selecione um usuário na lista.")
            return None
        idx = sel[0]
        if idx < 0 or idx >= len(self.users_data):
            return None
        return self.users_data[idx]

    def _refresh_users(self):
        self._set_busy(True)
        self._log("Atualizando lista de usuários...", CYAN)

        def task():
            try:
                self._ssh_load_users()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_user_add(self):
        dlg = UserDialog(self.root, self.ssh, self.groups_list)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        username = data["username"]
        password = data["password"]
        primary = data["primary_group"]
        extra = data["extra_groups"]
        email = data["email"]

        self._set_busy(True)
        self._log(f"Criando usuário '{username}'...", CYAN)

        def task():
            try:
                self.ssh.exec_assert(
                    f"useradd -m -g '{primary}' -s /bin/bash '{username}'"
                )
                if password:
                    self.ssh.exec_assert(f"echo '{username}:{password}' | chpasswd")
                if extra:
                    for eg in [g.strip() for g in extra.split(",") if g.strip()]:
                        self.ssh.exec_assert(f"usermod -aG '{eg}' '{username}'")
                if password:
                    self.ssh.exec_assert(
                        f"(echo '{password}'; echo '{password}') | smbpasswd -a -s '{username}'"
                    )
                self.log_queue.put(("log", (f"Usuário '{username}' criado com sucesso", GREEN)))
                self._ssh_load_users()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_user_edit(self):
        user = self._get_selected_user()
        if not user:
            return
        dlg = UserDialog(self.root, self.ssh, self.groups_list, user)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        username = data["username"]
        password = data["password"]
        primary = data["primary_group"]
        extra = data["extra_groups"]

        self._set_busy(True)
        self._log(f"Editando usuário '{username}'...", CYAN)

        def task():
            try:
                if primary:
                    self.ssh.exec_assert(f"usermod -g '{primary}' '{username}'")
                if extra:
                    self.ssh.exec_assert(f"usermod -G '{extra}' '{username}'")
                if password:
                    self.ssh.exec_assert(f"echo '{username}:{password}' | chpasswd")
                    self.ssh.exec_assert(
                        f"(echo '{password}'; echo '{password}') | smbpasswd -s '{username}'"
                    )
                self._ssh_load_users()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_user_delete(self):
        user = self._get_selected_user()
        if not user:
            return
        username = user["username"]
        if not messagebox.askyesno(
            "Confirmar exclusão",
            f"Tem certeza que deseja apagar o usuário '{username}'?\n\n"
            f"O diretório home será removido.",
            icon="warning",
        ):
            return

        self._set_busy(True)
        self._log(f"Apagando usuário '{username}'...", RED)

        def task():
            try:
                self.ssh.exec_assert(f"pdbedit -x '{username}' 2>/dev/null", timeout=10)
                self.ssh.exec_assert(
                    f"userdel -r '{username}' 2>/dev/null || userdel '{username}'", timeout=15
                )
                self.log_queue.put(("log", (f"Usuário '{username}' removido", YELLOW)))
                self._ssh_load_users()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    # ===================================================================
    # FOLDER CRUD
    # ===================================================================

    def _get_selected_folder(self):
        sel = self.list_folders.curselection()
        if not sel:
            messagebox.showwarning("Seleção", "Selecione uma pasta na lista.")
            return None
        idx = sel[0]
        if idx < 0 or idx >= len(self.folders_data):
            return None
        return self.folders_data[idx]

    def _refresh_folders(self):
        self._set_busy(True)
        self._log("Atualizando lista de pastas...", CYAN)

        def task():
            try:
                self._ssh_load_folders()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_folder_add(self):
        dlg = FolderDialog(self.root, self.ssh, self.groups_list, self.disks_list)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        name = data["name"]
        group = data["group"]
        perms = data["permissions"]
        disk_mount = data["disk_mount"]
        disk_uuid = data["disk_uuid"]
        create_smb = data["create_smb"]
        path = f"{disk_mount}/{name}"

        self._set_busy(True)
        self._log(f"Criando pasta '{name}' em {path}...", CYAN)

        def task():
            try:
                self.ssh.exec_assert(f"mkdir -p '{path}'")
                self.ssh.exec_assert(f"chown root:'{group}' '{path}'")
                self.ssh.exec_assert(f"chmod {perms} '{path}'")
                self.ssh.exec_assert(f"chmod g+s '{path}'")

                sf_uuid = str(__import__("uuid").uuid4())
                smb_uuid = str(__import__("uuid").uuid4()) if create_smb else ""

                lines = [
                    "import xml.etree.ElementTree as ET",
                    "import sys",
                    f"sf_uuid = '{sf_uuid}'",
                    f"smb_uuid = '{smb_uuid}'",
                    "ET.register_namespace('', '')",
                    "t = ET.parse('/etc/openmediavault/config.xml')",
                    "r = t.getroot()",
                    "# shared folder",
                    "p = r.find('.//system/shares')",
                    "if p is None:",
                    "    sys_node = r.find('.//system')",
                    "    if sys_node is None: sys.exit(1)",
                    "    p = ET.SubElement(sys_node, 'shares')",
                    "nf = ET.SubElement(p, 'sharedfolder')",
                    "ET.SubElement(nf, 'uuid').text = sf_uuid",
                    f"ET.SubElement(nf, 'name').text = '{name}'",
                    "ET.SubElement(nf, 'comment').text = ''",
                    f"ET.SubElement(nf, 'mntentref').text = '{disk_uuid}'",
                    f"ET.SubElement(nf, 'reldirpath').text = '{name}/'",
                ]
                if create_smb:
                    lines += [
                        "# smb share",
                        "smb = r.find('.//services/smb')",
                        "if smb is None:",
                        "    svc = r.find('.//services')",
                        "    if svc is None: sys.exit(1)",
                        "    smb = ET.SubElement(svc, 'smb')",
                        "sh = smb.find('shares')",
                        "if sh is None:",
                        "    sh = ET.SubElement(smb, 'shares')",
                        "ns = ET.SubElement(sh, 'share')",
                        "ET.SubElement(ns, 'uuid').text = smb_uuid",
                        "ET.SubElement(ns, 'enable').text = '1'",
                        "ET.SubElement(ns, 'sharedfolderref').text = sf_uuid",
                        f"ET.SubElement(ns, 'comment').text = '{name}'",
                        "ET.SubElement(ns, 'guest').text = 'no'",
                        "ET.SubElement(ns, 'readonly').text = '0'",
                        "ET.SubElement(ns, 'browseable').text = 'true'",
                        "ET.SubElement(ns, 'recyclebin').text = '0'",
                        "ET.SubElement(ns, 'recyclemaxsize').text = '0'",
                        "ET.SubElement(ns, 'recyclemaxage').text = '0'",
                        "ET.SubElement(ns, 'hidedotfiles').text = '1'",
                        "ET.SubElement(ns, 'inheritacls').text = 'false'",
                        "ET.SubElement(ns, 'inheritpermissions').text = 'false'",
                        "ET.SubElement(ns, 'easupport').text = '1'",
                        "ET.SubElement(ns, 'storedosattributes').text = '0'",
                        "ET.SubElement(ns, 'hostsallow').text = ''",
                        "ET.SubElement(ns, 'hostsdeny').text = ''",
                        "ET.SubElement(ns, 'audit').text = '0'",
                        "ET.SubElement(ns, 'timemachine').text = '0'",
                        "ET.SubElement(ns, 'timemachinemaxsize').text = ''",
                        "ET.SubElement(ns, 'transportencryption').text = '0'",
                        "ET.SubElement(ns, 'followsymlinks').text = '1'",
                        "ET.SubElement(ns, 'widelinks').text = '0'",
                        "ET.SubElement(ns, 'extraoptions').text = ''",
                    ]
                lines += [
                    "t.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)",
                ]
                self.ssh.exec_python("\n".join(lines))

                if create_smb:
                    code, out, err = self.ssh.exec("omv-salt deploy run samba 2>&1", timeout=120)
                    if code != 0:
                        self.log_queue.put(("log", (
                            f"omv-salt retornou {code}: {err[:200]}", YELLOW
                        )))
                    self.ssh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)
                    self.log_queue.put(("log", (
                        f"Pasta '{name}' criada com compartilhamento SMB", GREEN
                    )))
                else:
                    self.log_queue.put(("log", (f"Pasta '{name}' criada em {path}", GREEN)))
                self._ssh_load_folders()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_folder_edit(self):
        folder = self._get_selected_folder()
        if not folder:
            return
        dlg = FolderDialog(self.root, self.ssh, self.groups_list, self.disks_list, folder)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        name = data["name"]
        group = data["group"]
        perms = data["permissions"]
        path = folder["path"]

        self._set_busy(True)
        self._log(f"Editando pasta '{name}'...", CYAN)

        def task():
            try:
                if group:
                    self.ssh.exec_assert(f"chown root:'{group}' '{path}'")
                self.ssh.exec_assert(f"chmod {perms} '{path}'")
                self.log_queue.put(("log", (f"Pasta '{name}' atualizada", GREEN)))
                self._ssh_load_folders()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_folder_delete(self):
        folder = self._get_selected_folder()
        if not folder:
            return
        name = folder["name"]
        path = folder["path"]
        if not messagebox.askyesno(
            "Confirmar exclusão",
            f"Tem certeza que deseja apagar a pasta '{name}'?\n\n"
            f"Localização: {path}\n"
            f"O compartilhamento SMB também será removido.",
            icon="warning",
        ):
            return

        self._set_busy(True)
        self._log(f"Apagando pasta '{name}'...", RED)

        def task():
            try:
                script = (
                    "import xml.etree.ElementTree as ET\n"
                    "ET.register_namespace('', '')\n"
                    "t = ET.parse('/etc/openmediavault/config.xml')\n"
                    "r = t.getroot()\n"
                    f"sf = r.find(\".//sharedfolder[name='{name}']\")\n"
                    "if sf is not None:\n"
                    "    sf_uuid = sf.find('uuid')\n"
                    "    sfref = sf_uuid.text if sf_uuid is not None else ''\n"
                    "    # remove SMB shares that reference this folder\n"
                    "    for sh in r.findall(\".//share[sharedfolderref='\" + sfref + \"']\"):\n"
                    "        parent = r.find('.//services/smb/shares')\n"
                    "        if parent is not None and sh in list(parent):\n"
                    "            parent.remove(sh)\n"
                    "    # remove the shared folder\n"
                    "    p = r.find('.//system/shares')\n"
                    "    if p is not None and sf in list(p):\n"
                    "        p.remove(sf)\n"
                    "t.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)\n"
                )
                self.ssh.exec_python(script)
                self.ssh.exec_assert(f"rm -rf '{path}'", timeout=30)
                self.ssh.exec("omv-salt deploy run samba 2>&1", timeout=120)
                self.ssh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)
                self.log_queue.put(("log", (f"Pasta '{name}' removida", YELLOW)))
                self._ssh_load_folders()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    # ===================================================================
    # GROUP CRUD
    # ===================================================================

    def _get_selected_group(self):
        sel = self.list_groups_tab.curselection()
        if not sel:
            messagebox.showwarning("Seleção", "Selecione um grupo na lista.")
            return None
        idx = sel[0]
        if idx < 0 or idx >= len(self.groups_data):
            return None
        return self.groups_data[idx]

    def _refresh_groups(self):
        self._set_busy(True)
        self._log("Atualizando lista de grupos...", CYAN)

        def task():
            try:
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_group_add(self):
        dlg = GroupDialog(self.root, self.ssh, self.users_data)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        name = data["name"]
        comment = data["comment"]
        members = data["members"]

        self._set_busy(True)
        self._log(f"Criando grupo '{name}'...", CYAN)

        def task():
            try:
                self.ssh.exec_assert(f"groupadd '{name}'")
                for m in members:
                    self.ssh.exec_assert(f"usermod -aG '{name}' '{m}'")

                name_b64 = base64.b64encode(name.encode()).decode()
                comment_b64 = base64.b64encode(comment.encode()).decode()
                script = (
                    "import xml.etree.ElementTree as ET\n"
                    "import base64\n"
                    f"n = base64.b64decode('{name_b64}').decode()\n"
                    f"c = base64.b64decode('{comment_b64}').decode()\n"
                    "ET.register_namespace('', '')\n"
                    "t = ET.parse('/etc/openmediavault/config.xml')\n"
                    "r = t.getroot()\n"
                    "p = r.find('.//system/usermanagement/groups')\n"
                    "g = ET.SubElement(p, 'group')\n"
                    "ET.SubElement(g, 'uuid').text = str(__import__('uuid').uuid4())\n"
                    "ET.SubElement(g, 'name').text = n\n"
                    "ET.SubElement(g, 'comment').text = c\n"
                    "t.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)\n"
                )
                self.ssh.exec_python(script)
                self.log_queue.put(("log", (f"Grupo '{name}' criado com sucesso", GREEN)))
                self._ssh_load_groups_list()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_group_edit(self):
        group = self._get_selected_group()
        if not group:
            return
        dlg = GroupDialog(self.root, self.ssh, self.users_data, group)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        data = dlg.result
        name = data["name"]
        comment = data["comment"]
        new_members = data["members"]
        old_members = group.get("members", [])

        self._set_busy(True)
        self._log(f"Editando grupo '{name}'...", CYAN)

        def task():
            try:
                added = [m for m in new_members if m not in old_members]
                removed = [m for m in old_members if m not in new_members]
                for m in removed:
                    self.ssh.exec_assert(
                        f"gpasswd -d '{m}' '{name}' 2>/dev/null || deluser '{m}' '{name}' 2>/dev/null || true",
                        timeout=10,
                    )
                for m in added:
                    self.ssh.exec_assert(f"usermod -aG '{name}' '{m}'")

                name_b64 = base64.b64encode(name.encode()).decode()
                comment_b64 = base64.b64encode(comment.encode()).decode()
                script = (
                    "import xml.etree.ElementTree as ET\n"
                    "import base64\n"
                    f"name = base64.b64decode('{name_b64}').decode()\n"
                    f"comment = base64.b64decode('{comment_b64}').decode()\n"
                    "ET.register_namespace('', '')\n"
                    "t = ET.parse('/etc/openmediavault/config.xml')\n"
                    "r = t.getroot()\n"
                    "g = r.find(\".//system/usermanagement/groups/group[name='\" + name + \"']\")\n"
                    "if g is not None:\n"
                    "    c = g.find('comment')\n"
                    "    if c is not None:\n"
                    "        c.text = comment\n"
                    "    else:\n"
                    "        ET.SubElement(g, 'comment').text = comment\n"
                    "t.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)\n"
                )
                self.ssh.exec_python(script)
                self.log_queue.put(("log", (f"Grupo '{name}' atualizado", GREEN)))
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def _on_group_delete(self):
        group = self._get_selected_group()
        if not group:
            return
        name = group["name"]
        if not messagebox.askyesno(
            "Confirmar exclusão",
            f"Tem certeza que deseja apagar o grupo '{name}'?\n\n"
            f"O grupo será removido do sistema e do OMV.",
            icon="warning",
        ):
            return

        self._set_busy(True)
        self._log(f"Apagando grupo '{name}'...", RED)

        def task():
            try:
                name_b64 = base64.b64encode(name.encode()).decode()
                script = (
                    "import xml.etree.ElementTree as ET\n"
                    "import base64\n"
                    f"n = base64.b64decode('{name_b64}').decode()\n"
                    "ET.register_namespace('', '')\n"
                    "t = ET.parse('/etc/openmediavault/config.xml')\n"
                    "r = t.getroot()\n"
                    "g = r.find(\".//system/usermanagement/groups/group[name='\" + n + \"']\")\n"
                    "if g is not None:\n"
                    "    p = r.find('.//system/usermanagement/groups')\n"
                    "    if p is not None:\n"
                    "        p.remove(g)\n"
                    "t.write('/etc/openmediavault/config.xml', encoding='UTF-8', xml_declaration=True)\n"
                )
                self.ssh.exec_python(script)
                self.ssh.exec_assert(f"groupdel '{name}' 2>/dev/null || true", timeout=10)
                self.log_queue.put(("log", (f"Grupo '{name}' removido", YELLOW)))
                self._ssh_load_groups_list()
                self._ssh_load_groups_full()
            except Exception as e:
                self.log_queue.put(("error", str(e)))
            finally:
                self.log_queue.put(("done", None))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    # ===================================================================
    # EXPORT / IMPORT
    # ===================================================================

    def _on_export(self):
        if not self.connected or self.busy:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Backup", "*.json")],
            title="Exportar configuração",
            parent=self.root,
        )
        if not path:
            return
        data = {
            "version": "1.0",
            "exported_at": __import__("datetime").datetime.now().isoformat(),
            "server": self.lbl_hostname.cget("text") if self.connected else "",
            "groups": [
                {"name": g["name"], "comment": g.get("comment", ""),
                 "members": g.get("members", [])}
                for g in self.groups_data
            ],
            "users": [
                {"username": u["username"],
                 "primary_group": u["primary_group"],
                 "extra_groups": [g.strip() for g in u.get("groups", "").split(",") if g.strip()],
                 "email": u.get("gecos", "")}
                for u in self.users_data
            ],
            "folders": [
                {"name": f["name"], "group": f.get("group", ""),
                 "permissions": f.get("permissions", ""),
                 "disk": f.get("disk", ""),
                 "comment": f.get("comment", ""),
                 "path": f.get("path", "")}
                for f in self.folders_data
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._log(f"Configuração exportada: {path}", GREEN)
        except Exception as e:
            self._log(f"Erro ao exportar: {e}", RED)

    def _on_import(self):
        if not self.connected or self.busy:
            return
        path = filedialog.askopenfilename(
            filetypes=[("JSON Backup", "*.json")],
            title="Importar configuração",
            parent=self.root,
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao ler arquivo:\n{e}", parent=self.root)
            return
        if not any(k in data for k in ("groups", "users", "folders")):
            messagebox.showerror("Erro", "Arquivo de backup inválido.",
                                 parent=self.root)
            return
        if not messagebox.askyesno(
            "Confirmar importação",
            "Isso criará grupos, usuários e pastas no servidor.\n"
            "Usuários serão criados SEM senha — defina senhas\n"
            "manualmente após a importação.\n\n"
            "Deseja continuar?",
            parent=self.root,
        ):
            return
        self._set_busy(True)
        self._log("Importando configuração...", CYAN)
        t = threading.Thread(target=self._import_apply, args=(data,), daemon=True)
        t.start()

    def _import_apply(self, data):
        L = lambda msg, color=FG: self.log_queue.put(("log", (msg, color)))
        try:
            disk_by_mount = {d["mount"]: d for d in self.disks_list}
            groups = data.get("groups", [])
            users = data.get("users", [])
            folders = data.get("folders", [])
            ok = 0

            # ---- GRUPOS ----
            L(f"Importando {len(groups)} grupo(s)...", CYAN)
            for g in groups:
                name = g["name"]
                comment = g.get("comment", "")
                members = g.get("members", [])
                code, _, _ = self.ssh.exec(
                    f"getent group '{name}' >/dev/null 2>&1", timeout=5
                )
                if code == 0:
                    L(f"  Grupo '{name}' já existe", YELLOW)
                    ok += 1
                    continue
                self.ssh.exec_assert(f"groupadd '{name}'", timeout=10)
                for m in members:
                    self.ssh.exec(f"usermod -aG '{name}' '{m}'", timeout=10)
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
                    "g=ET.SubElement(p,'group')\n"
                    "ET.SubElement(g,'uuid').text=str(__import__('uuid').uuid4())\n"
                    "ET.SubElement(g,'name').text=n\n"
                    "ET.SubElement(g,'comment').text=c\n"
                    "t.write('/etc/openmediavault/config.xml',encoding='UTF-8',xml_declaration=True)\n"
                )
                self.ssh.exec_python(script)
                L(f"  Grupo '{name}' criado", GREEN)
                ok += 1
            L(f"{ok} grupo(s) processados", GREEN if ok else YELLOW)

            # ---- USUÁRIOS ----
            ok = 0
            L(f"Importando {len(users)} usuário(s)...", CYAN)
            for u in users:
                username = u["username"]
                primary = u.get("primary_group", "users")
                extra = u.get("extra_groups", [])
                code, _, _ = self.ssh.exec(
                    f"id '{username}' >/dev/null 2>&1", timeout=5
                )
                if code == 0:
                    L(f"  Usuário '{username}' já existe", YELLOW)
                    ok += 1
                    continue
                self.ssh.exec_assert(
                    f"useradd -m -g '{primary}' -s /bin/bash '{username}'", timeout=10
                )
                for eg in extra:
                    if eg.strip():
                        self.ssh.exec(f"usermod -aG '{eg.strip()}' '{username}'", timeout=10)
                L(f"  Usuário '{username}' criado — definir senha em Editar", GREEN)
                ok += 1
            L(f"{ok} usuário(s) processados", GREEN if ok else YELLOW)

            # ---- PASTAS ----
            ok = 0
            smb_needed = False
            L(f"Importando {len(folders)} pasta(s)...", CYAN)
            for f in folders:
                name = f["name"]
                group = f.get("group", "users")
                perms = f.get("permissions", "770")
                disk_mount = f.get("disk", "")
                comment = f.get("comment", "")
                if isinstance(perms, str) and not re.match(r"^\d{3,4}$", perms):
                    perms = _sym_to_octal(perms)
                disk = disk_by_mount.get(disk_mount, {})
                if not disk.get("mount"):
                    L(f"  Disco '{disk_mount}' não encontrado — pasta '{name}' ignorada", RED)
                    continue
                path = f"{disk['mount']}/{name}"
                code, _, _ = self.ssh.exec(f"test -d '{path}'", timeout=5)
                if code == 0:
                    L(f"  Pasta '{name}' já existe em {path}", YELLOW)
                    ok += 1
                    continue
                self.ssh.exec_assert(f"mkdir -p '{path}'", timeout=10)
                self.ssh.exec_assert(f"chown root:'{group}' '{path}'", timeout=10)
                self.ssh.exec_assert(f"chmod {perms} '{path}'", timeout=10)
                self.ssh.exec_assert(f"chmod g+s '{path}'", timeout=10)
                sf_uuid = str(__import__("uuid").uuid4())
                smb_uuid = str(__import__("uuid").uuid4())
                script = (
                    "import xml.etree.ElementTree as ET\n"
                    "ET.register_namespace('', '')\n"
                    "t=ET.parse('/etc/openmediavault/config.xml')\n"
                    "r=t.getroot()\n"
                    "p=r.find('.//system/shares')\n"
                    "if p is None:\n"
                    "    sn=r.find('.//system')\n"
                    "    if sn is None:\n"
                    "        raise Exception('no system node')\n"
                    "    p=ET.SubElement(sn,'shares')\n"
                    "nf=ET.SubElement(p,'sharedfolder')\n"
                    f"ET.SubElement(nf,'uuid').text='{sf_uuid}'\n"
                    f"ET.SubElement(nf,'name').text='{name}'\n"
                    f"ET.SubElement(nf,'comment').text='{comment}'\n"
                    f"ET.SubElement(nf,'mntentref').text='{disk["uuid"]}'\n"
                    f"ET.SubElement(nf,'reldirpath').text='{name}/'\n"
                    "# SMB share\n"
                    "smb=r.find('.//services/smb')\n"
                    "if smb is None:\n"
                    "    svc=r.find('.//services')\n"
                    "    if svc is None:\n"
                    "        raise Exception('no services node')\n"
                    "    smb=ET.SubElement(svc,'smb')\n"
                    "sh=smb.find('shares')\n"
                    "if sh is None:\n"
                    "    sh=ET.SubElement(smb,'shares')\n"
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
                self.ssh.exec_python(script)
                L(f"  Pasta '{name}' criada com SMB", GREEN)
                ok += 1
                smb_needed = True

            if smb_needed:
                L("Aplicando configuração SMB via omv-salt...", CYAN)
                code, out, err = self.ssh.exec("omv-salt deploy run samba 2>&1", timeout=120)
                if code != 0:
                    L(f"omv-salt retornou {code}: {err[:200] if err else ''}", YELLOW)
                self.ssh.exec("systemctl restart smbd nmbd 2>/dev/null || true", timeout=15)

            L(f"{ok} pasta(s) processadas", GREEN if ok else YELLOW)
            L("Importação concluída! Recarregando dados...", GREEN)
            self._ssh_load_groups_list()
            self._ssh_load_groups_full()
            self._ssh_load_users()
            self._ssh_load_folders()
        except Exception as e:
            self.log_queue.put(("error", str(e)))
        finally:
            self.log_queue.put(("done", None))

    # ===================================================================
    # RUN
    # ===================================================================

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    OMVGUI().run()
