import os
import re
import uuid
import base64
import subprocess
import tempfile


class LocalShell:
    """Drop-in replacement for SSHClient — runs commands directly on the local
    machine via subprocess.  Same interface, zero SSH dependencies."""

    def __init__(self, config=None, dry_run=False):
        self.config = config
        self.dry_run = dry_run

    def connect(self):
        pass

    def close(self):
        pass

    # -----------------------------------------------------------------
    # Core execution
    # -----------------------------------------------------------------

    def exec(self, command, sudo=False, timeout=60):
        if self.dry_run:
            return (0, "", "")
        if sudo and os.geteuid() != 0:
            command = f"sudo {command}"
        try:
            r = subprocess.run(
                command, shell=True,
                capture_output=True, text=True,
                timeout=timeout,
            )
            return (r.returncode, r.stdout.strip(), r.stderr.strip())
        except subprocess.TimeoutExpired:
            return (-1, "", f"Timeout after {timeout}s")
        except Exception as e:
            return (-1, "", str(e))

    def exec_assert(self, command, sudo=False, timeout=60):
        code, out, err = self.exec(command, sudo, timeout)
        if code != 0 and err:
            print(f"  [WARN] comando retornou {code}: {err[:200]}")
        return out

    # -----------------------------------------------------------------
    # File / Python helpers
    # -----------------------------------------------------------------

    def write_remote_file_sftp(self, remote_path, content):
        try:
            with open(remote_path, "w") as f:
                f.write(content)
        except Exception:
            enc = base64.b64encode(content.encode()).decode()
            self.exec(
                "python3 -c "
                f"\"import base64; open('{remote_path}','w').write(base64.b64decode('{enc}').decode())\"",
            )

    def exec_python(self, code):
        tmp = f"/tmp/omw_{uuid.uuid4().hex[:8]}.py"
        try:
            with open(tmp, "w") as f:
                f.write(code)
            return self.exec(f"python3 {tmp}", timeout=60)
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Existence checks
    # -----------------------------------------------------------------

    def file_exists(self, path):
        return os.path.isfile(path)

    def dir_exists(self, path):
        return os.path.isdir(path)

    def group_exists(self, name):
        _, out, _ = self.exec(
            f"getent group '{name}' >/dev/null 2>&1 && echo YES || echo NO",
        )
        return "YES" in out

    def user_exists(self, name):
        _, out, _ = self.exec(
            f"id '{name}' 2>/dev/null && echo YES || echo NO",
        )
        return "YES" in out

    def samba_user_exists(self, name):
        _, out, _ = self.exec(
            f"pdbedit -L 2>/dev/null | grep -q '^{name}:' && echo YES || echo NO",
        )
        return "YES" in out

    def xml_exists(self, xpath):
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
