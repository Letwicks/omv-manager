#!/bin/bash
###############################################################################
# OMV Manager - Gerenciamento de Usuários, Pastas e Permissões
# OpenMediaVault 8.x - Debian 13
# Uso: ./omv-manager.sh [opção]
#   Sem argumentos: Executa todas as operações configuradas
#   --dry-run:     Mostra o que será feito sem executar
#   --status:      Mostra status atual de usuários, grupos e pastas
#   --help:        Mostra esta mensagem
###############################################################################

set -uo pipefail

# ============================================================================
# CONFIGURAÇÃO - Edite aqui conforme necessário
# ============================================================================

BASE="/srv/dev-disk-by-uuid-cdc81bc1-9210-48b1-a342-28f7ce43fdd0"
MNTENTREF="3fe14629-114d-440c-aa00-56581fd52ef5"
WORKGROUP="WORKGROUP"
CONFIG="/etc/openmediavault/config.xml"

# Formato: "nome:senha:grupo_primario:grupos_adicionais:email"
# grupos_adicionais separados por vírgula (sem espaços)
USERS=(
  "joao:senha123:engenharia:administracao:joao@empresa.com"
  "maria:senha456:administracao::maria@empresa.com"
  "pedro:senha789:seguranca::pedro@empresa.com"
  "ana:senha000:engenharia::ana@empresa.com"
)

# Formato: "nome:comentario:membros"
# membros separados por vírgula (sem espaços)
GROUP_LIST=(
  "engenharia:Departamento de Engenharia:joao,ana"
  "administracao:Departamento Administrativo:maria"
  "seguranca:Departamento de Seguranca:pedro"
  "rh:Recursos Humanos:maria,ana"
)

# Formato: "nome:grupo_proprietario:perm_proprietario:perm_grupo:perm_outros"
# Permissões: rwx=7, rw=6, rx=5, r=4, etc.
FOLDERS=(
  "engenharia:engenharia:rwx:rwx:---"
  "administracao:administracao:rwx:rwx:---"
  "seguranca:seguranca:rwx:rwx:---"
  "rh:rh:rwx:rwx:---"
  "publico:users:rwx:rwx:rwx"
  "projetos:engenharia:rwx:rwx:---"
)

# Formato: "pasta:acl_user:permissao" ou "pasta:acl_group:permissao"
# Onde permissao é rwx, rx, rw, etc.
FOLDER_ACLS=(
  "engenharia:user:joao:rwx"
  "engenharia:user:ana:rwx"
  "engenharia:group:administracao:rx"
  "administracao:user:maria:rwx"
  "administracao:group:rh:rx"
  "projetos:user:joao:rwx"
  "projetos:user:ana:rwx"
  "projetos:group:rh:rx"
)

# Formato: "nome_pasta:comentario:tipo_guest:visivel:herdar_acl"
# tipo_guest: allow = guest ok, no = sem guest
# visivel: 1 = browseable, 0 = oculto
SMB_SHARES=(
  "publico:Compartilhamento Publico:allow:1:0"
  "engenharia:Arquivos Engenharia:no:1:1"
  "administracao:Arquivos Administrativos:no:1:1"
  "seguranca:Arquivos Seguranca:no:1:1"
  "rh:Arquivos RH:no:1:1"
  "projetos:Projetos da Empresa:no:1:1"
)

# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

DRY_RUN=false

uuidgen() {
  cat /proc/sys/kernel/random/uuid
}

run_cmd() {
  if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} $1"
  else
    eval "$1"
  fi
}

# Parser de campos separados por ":"
field() {
  echo "$1" | cut -d: -f"$2"
}

# Permissão simbólica para numérica (rwx -> 7, rx -> 5, etc)
perm_to_num() {
  local p="$1" num=0
  [[ "$p" == *r* ]] && num=$((num + 4))
  [[ "$p" == *w* ]] && num=$((num + 2))
  [[ "$p" == *x* ]] && num=$((num + 1))
  echo "$num"
}

# Verifica se um elemento existe no XML pelo xpath
xml_exists() {
  local xpath="$1"
  local count
  count=$(xmlstarlet sel -t -v "count($xpath)" "$CONFIG" 2>/dev/null || echo "0")
  [ "$count" -gt 0 ]
}

# Gera XML de privilégios baseado nas ACLs configuradas para uma pasta
gen_privileges_xml() {
  local folder_name="$1" result=""
  for acl in "${FOLDER_ACLS[@]}"; do
    local af mt at ap
    af=$(field "$acl" 1)
    mt=$(field "$acl" 2)
    at=$(field "$acl" 3)
    ap=$(field "$acl" 4)
    if [ "$af" = "$folder_name" ]; then
      local perm_num
      perm_num=$(perm_to_num "$ap")
      result="$result<privilege><type>$mt</type><name>$at</name><perms>$perm_num</perms></privilege>"
    fi
  done
  echo "$result"
}

# ============================================================================
# STATUS
# ============================================================================

show_status() {
  echo -e "\n${CYAN}========== USUÁRIOS DO SISTEMA ==========${NC}"
  printf "%-20s %-10s %-20s\n" "USUARIO" "UID" "GRUPOS"
  for u in "${USERS[@]}"; do
    local username
    username=$(field "$u" 1)
    if id "$username" >/dev/null 2>&1; then
      local uid grupos
      uid=$(id -u "$username" 2>/dev/null)
      grupos=$(groups "$username" 2>/dev/null | cut -d: -f2 | xargs)
      printf "%-20s %-10s %-20s\n" "$username" "$uid" "${grupos:0:50}"
    fi
  done

  echo -e "\n${CYAN}========== GRUPOS DO SISTEMA ==========${NC}"
  printf "%-20s %-10s %-20s\n" "GRUPO" "GID" "MEMBROS"
  for g in "${GROUP_LIST[@]}"; do
    local gname
    gname=$(field "$g" 1)
    if getent group "$gname" >/dev/null 2>&1; then
      local gid members
      gid=$(getent group "$gname" | cut -d: -f3)
      members=$(getent group "$gname" | cut -d: -f4)
      printf "%-20s %-10s %-20s\n" "$gname" "$gid" "${members:0:50}"
    fi
  done

  echo -e "\n${CYAN}========== PASTAS COMPARTILHADAS ==========${NC}"
  for f in "${FOLDERS[@]}"; do
    local fname
    fname=$(field "$f" 1)
    local folder_path="$BASE/$fname"
    if [ -d "$folder_path" ]; then
      local perms
      perms=$(stat -c "%A %U:%G" "$folder_path" 2>/dev/null)
      echo -e "  ${GREEN}$folder_path${NC}  ($perms)"
    else
      echo -e "  ${RED}$folder_path${NC}  (NÃO EXISTE)"
    fi
  done

  echo -e "\n${CYAN}========== SHARES SMB ==========${NC}"
  if xml_exists "//config/services/smb/shares/share"; then
    xmlstarlet sel -t -m "//config/services/smb/shares/share" \
      -v "concat('  ', uuid, ' [', guest, '] ', comment)" \
      -n "$CONFIG" 2>/dev/null
  else
    echo "  (Nenhum)"
  fi
}

# ============================================================================
# CRIAÇÃO DE GRUPOS (Linux)
# ============================================================================

create_groups() {
  log_step "Criando grupos no sistema..."
  for g in "${GROUP_LIST[@]}"; do
    local gname comment members
    gname=$(field "$g" 1)
    comment=$(field "$g" 2)
    members=$(field "$g" 3)
    if getent group "$gname" >/dev/null 2>&1; then
      log_info "Grupo '$gname' já existe no sistema"
    else
      run_cmd "groupadd '$gname'"
      log_info "Grupo '$gname' criado no sistema"
    fi
    if [ -n "$members" ]; then
      OLD_IFS="$IFS"; IFS=','
      for m in $members; do
        if id "$m" >/dev/null 2>&1; then
          run_cmd "usermod -aG '$gname' '$m'"
          log_info "  Usuário '$m' adicionado ao grupo '$gname'"
        fi
      done
      IFS="$OLD_IFS"
    fi
  done
}

# ============================================================================
# CRIAÇÃO DE GRUPOS (OMV)
# ============================================================================

create_omv_groups() {
  log_step "Registrando grupos no OMV..."
  for g in "${GROUP_LIST[@]}"; do
    local gname comment
    gname=$(field "$g" 1)
    comment=$(field "$g" 2)
    if xml_exists "//config/system/usermanagement/groups/group[name='$gname']"; then
      log_info "Grupo OMV '$gname' já registrado"
    else
      local new_uuid
      new_uuid=$(uuidgen)
      local fragment
      fragment="<group><uuid>$new_uuid</uuid><name>$gname</name><comment>$comment</comment></group>"
      run_cmd "xmlstarlet ed -L -s '//config/system/usermanagement/groups' -t 'text' -n '' -v '$fragment' '$CONFIG' 2>/dev/null; xmlstarlet ed -L --inplace 's|<group><uuid>|<group><uuid>|' '$CONFIG' 2>/dev/null; true"
      # More robust approach: use Python to add XML properly
      run_cmd "python3 -c \"
import xml.etree.ElementTree as ET
ET.register_namespace('', '')
tree = ET.parse('$CONFIG')
root = tree.getroot()
ns = {'': ''}
parent = root.find('.//system/usermanagement/groups')
new = ET.SubElement(parent, 'group')
ET.SubElement(new, 'uuid').text = '$new_uuid'
ET.SubElement(new, 'name').text = '$gname'
ET.SubElement(new, 'comment').text = '$comment'
tree.write('$CONFIG', encoding='UTF-8', xml_declaration=True)
print('Grupo OMV \\'$gname\\' registrado (UUID: $new_uuid)')
\""
    fi
  done
}

# ============================================================================
# CRIAÇÃO DE USUÁRIOS (Linux)
# ============================================================================

create_users() {
  log_step "Criando usuários no sistema..."
  for u in "${USERS[@]}"; do
    local username password primary_group extra_groups email
    username=$(field "$u" 1)
    password=$(field "$u" 2)
    primary_group=$(field "$u" 3)
    extra_groups=$(field "$u" 4)
    email=$(field "$u" 5)
    if id "$username" >/dev/null 2>&1; then
      log_info "Usuário '$username' já existe no sistema"
    else
      if [ -n "$primary_group" ] && getent group "$primary_group" >/dev/null 2>&1; then
        run_cmd "useradd -m -g '$primary_group' -s /bin/bash -c '$username' '$username'"
      else
        run_cmd "useradd -m -s /bin/bash -c '$username' '$username'"
      fi
      if [ -n "$password" ]; then
        run_cmd "echo '$username:$password' | chpasswd"
      fi
      log_info "Usuário '$username' criado no sistema"
    fi
    if [ -n "$extra_groups" ]; then
      OLD_IFS="$IFS"; IFS=','
      for eg in $extra_groups; do
        if getent group "$eg" >/dev/null 2>&1; then
          run_cmd "usermod -aG '$eg' '$username'"
          log_info "  Usuário '$username' adicionado ao grupo '$eg'"
        fi
      done
      IFS="$OLD_IFS"
    fi
    if command -v smbpasswd >/dev/null 2>&1; then
      if pdbedit -L 2>/dev/null | grep -q "^$username:"; then
        log_info "  Usuário Samba '$username' já existe"
      else
        if [ -n "$password" ]; then
          run_cmd "(echo '$password'; echo '$password') | smbpasswd -a -s '$username'"
          log_info "  Senha Samba definida para '$username'"
        fi
      fi
    fi
  done
}

# ============================================================================
# CRIAÇÃO DE USUÁRIOS (OMV)
# ============================================================================

create_omv_users() {
  log_step "Registrando usuários no OMV..."
  for u in "${USERS[@]}"; do
    local username email
    username=$(field "$u" 1)
    email=$(field "$u" 5)
    if xml_exists "//config/system/usermanagement/users/user[name='$username']"; then
      log_info "Usuário OMV '$username' já registrado"
    else
      local new_uuid
      new_uuid=$(uuidgen)
      run_cmd "python3 -c \"
import xml.etree.ElementTree as ET
ET.register_namespace('', '')
tree = ET.parse('$CONFIG')
root = tree.getroot()
parent = root.find('.//system/usermanagement/users')
new = ET.SubElement(parent, 'user')
ET.SubElement(new, 'uuid').text = '$new_uuid'
ET.SubElement(new, 'name').text = '$username'
ET.SubElement(new, 'email').text = '$email'
ET.SubElement(new, 'disallowusermod').text = '0'
sshkeys = ET.SubElement(new, 'sshpubkeys')
ET.SubElement(sshkeys, 'sshpubkey')
tree.write('$CONFIG', encoding='UTF-8', xml_declaration=True)
print('Usuário OMV \\'$username\\' registrado (UUID: $new_uuid)')
\""
    fi
  done
}

# ============================================================================
# CRIAÇÃO DE PASTAS
# ============================================================================

create_folders() {
  log_step "Criando pastas compartilhadas..."
  for f in "${FOLDERS[@]}"; do
    local fname group_name perm_owner perm_group perm_other
    fname=$(field "$f" 1)
    group_name=$(field "$f" 2)
    perm_owner=$(field "$f" 3)
    perm_group=$(field "$f" 4)
    perm_other=$(field "$f" 5)
    local folder_path="$BASE/$fname"
    if [ -d "$folder_path" ]; then
      log_info "Pasta '$folder_path' já existe"
    else
      run_cmd "mkdir -p '$folder_path'"
      log_info "Pasta '$folder_path' criada"
    fi
    if getent group "$group_name" >/dev/null 2>&1; then
      run_cmd "chown root:'$group_name' '$folder_path'"
    fi
    local p_on p_gn p_ot
    p_on=$(perm_to_num "$perm_owner")
    p_gn=$(perm_to_num "$perm_group")
    p_ot=$(perm_to_num "$perm_other")
    run_cmd "chmod ${p_on}${p_gn}${p_ot} '$folder_path'"
    run_cmd "chmod g+s '$folder_path'"
    log_info "  Permissões: $perm_owner ($p_on) / $perm_group ($p_gn) / $perm_other ($p_ot) + setgid"
  done
}

# ============================================================================
# ACLs
# ============================================================================

set_acls() {
  log_step "Configurando ACLs..."
  for acl in "${FOLDER_ACLS[@]}"; do
    local folder type target perm
    folder=$(field "$acl" 1)
    type=$(field "$acl" 2)
    target=$(field "$acl" 3)
    perm=$(field "$acl" 4)
    local folder_path="$BASE/$folder"
    if [ ! -d "$folder_path" ]; then
      log_warn "Pasta '$folder_path' não existe, pulando ACL"
      continue
    fi
    local p_val
    p_val=$(perm_to_num "$perm")
    if [ "$type" = "user" ]; then
      if id "$target" >/dev/null 2>&1; then
        run_cmd "setfacl -m u:$target:$p_val '$folder_path'"
        run_cmd "setfacl -d -m u:$target:$p_val '$folder_path' 2>/dev/null || true"
        log_info "  ACL user '$target' = $perm ($p_val) em '$folder'"
      else
        log_warn "  Usuário '$target' não existe, pulando ACL"
      fi
    elif [ "$type" = "group" ]; then
      if getent group "$target" >/dev/null 2>&1; then
        run_cmd "setfacl -m g:$target:$p_val '$folder_path'"
        run_cmd "setfacl -d -m g:$target:$p_val '$folder_path' 2>/dev/null || true"
        log_info "  ACL group '$target' = $perm ($p_val) em '$folder'"
      else
        log_warn "  Grupo '$target' não existe, pulando ACL"
      fi
    fi
  done
}

# ============================================================================
# SHARED FOLDERS no OMV
# ============================================================================

create_omv_sharedfolders() {
  log_step "Registrando pastas compartilhadas no OMV..."
  for f in "${FOLDERS[@]}"; do
    local fname
    fname=$(field "$f" 1)
    if xml_exists "//config/system/shares/sharedfolder[name='$fname']"; then
      log_info "Sharedfolder OMV '$fname' já registrado"
    else
      local new_uuid
      new_uuid=$(uuidgen)
      local privileges
      privileges=$(gen_privileges_xml "$fname")
      run_cmd "python3 -c \"
import xml.etree.ElementTree as ET
ET.register_namespace('', '')
tree = ET.parse('$CONFIG')
root = tree.getroot()
parent = root.find('.//system/shares')
new = ET.SubElement(parent, 'sharedfolder')
ET.SubElement(new, 'uuid').text = '$new_uuid'
ET.SubElement(new, 'name').text = '$fname'
ET.SubElement(new, 'comment').text = ''
ET.SubElement(new, 'mntentref').text = '$MNTENTREF'
ET.SubElement(new, 'reldirpath').text = '${fname}/'
if '$privileges':
    priv_parent = ET.SubElement(new, 'privileges')
    for p_text in '''$privileges'''.split('</privilege>'):
        if not p_text.strip():
            continue
        p_text += '</privilege>'
        try:
            p_elem = ET.fromstring(p_text)
            priv_parent.append(p_elem)
        except:
            pass
tree.write('$CONFIG', encoding='UTF-8', xml_declaration=True)
print('Sharedfolder OMV \\'$fname\\' registrado (UUID: $new_uuid)')
\""
    fi
  done
}

# ============================================================================
# SHARES SMB no OMV
# ============================================================================

get_omv_sf_uuid() {
  local sf_name="$1"
  xmlstarlet sel -t -v "//config/system/shares/sharedfolder[name='$sf_name']/uuid" "$CONFIG" 2>/dev/null
}

create_smb_shares() {
  log_step "Criando compartilhamentos SMB no OMV..."
  for s in "${SMB_SHARES[@]}"; do
    local sname comment guest browseable inheritacl
    sname=$(field "$s" 1)
    comment=$(field "$s" 2)
    guest=$(field "$s" 3)
    browseable=$(field "$s" 4)
    inheritacl=$(field "$s" 5)
    local sf_uuid
    sf_uuid=$(get_omv_sf_uuid "$sname")
    if [ -z "$sf_uuid" ]; then
      log_warn "Sharedfolder '$sname' não encontrado no OMV, pulando SMB share"
      continue
    fi
    if xml_exists "//config/services/smb/shares/share[sharedfolderref='$sf_uuid']"; then
      log_info "SMB share '$sname' já existe"
      continue
    fi
    local new_uuid
    new_uuid=$(uuidgen)
    local bool_browse bool_inherit
    [ "$browseable" = "1" ] && bool_browse="true" || bool_browse="false"
    [ "$inheritacl" = "1" ] && bool_inherit="true" || bool_inherit="false"
    run_cmd "python3 -c \"
import xml.etree.ElementTree as ET
ET.register_namespace('', '')
tree = ET.parse('$CONFIG')
root = tree.getroot()
parent = root.find('.//services/smb/shares')
if parent is None:
    smb = root.find('.//services/smb')
    shares_container = ET.SubElement(smb, 'shares')
    parent = shares_container
new = ET.SubElement(parent, 'share')
ET.SubElement(new, 'uuid').text = '$new_uuid'
ET.SubElement(new, 'enable').text = '1'
ET.SubElement(new, 'sharedfolderref').text = '$sf_uuid'
ET.SubElement(new, 'comment').text = '$comment'
ET.SubElement(new, 'guest').text = '$guest'
ET.SubElement(new, 'readonly').text = '0'
ET.SubElement(new, 'browseable').text = '$bool_browse'
ET.SubElement(new, 'recyclebin').text = '0'
ET.SubElement(new, 'recyclemaxsize').text = '0'
ET.SubElement(new, 'recyclemaxage').text = '0'
ET.SubElement(new, 'hidedotfiles').text = '1'
ET.SubElement(new, 'inheritacls').text = '$bool_inherit'
ET.SubElement(new, 'inheritpermissions').text = '$bool_inherit'
ET.SubElement(new, 'easupport').text = '1'
ET.SubElement(new, 'storedosattributes').text = '0'
ET.SubElement(new, 'hostsallow').text = ''
ET.SubElement(new, 'hostsdeny').text = ''
ET.SubElement(new, 'audit').text = '0'
ET.SubElement(new, 'timemachine').text = '0'
ET.SubElement(new, 'timemachinemaxsize').text = ''
ET.SubElement(new, 'transportencryption').text = '0'
ET.SubElement(new, 'followsymlinks').text = '1'
ET.SubElement(new, 'widelinks').text = '0'
ET.SubElement(new, 'extraoptions').text = ''
tree.write('$CONFIG', encoding='UTF-8', xml_declaration=True)
print('SMB share \\'$sname\\' criado (UUID: $new_uuid)')
\""
    done
}

# ============================================================================
# APLICAR CONFIGURAÇÃO
# ============================================================================

apply_config() {
  log_step "Aplicando configurações com omv-salt..."
  run_cmd "omv-salt deploy run samba 2>&1 | tail -20"
  log_info "Configuração aplicada. Reiniciando serviços Samba..."
  run_cmd "systemctl restart smbd nmbd 2>/dev/null || true"
  log_info "Samba reiniciado com sucesso!"
}

# ============================================================================
# HELP
# ============================================================================

show_help() {
  cat << EOF
OMV Manager - Gerenciamento de Usuários, Pastas e Permissões no OpenMediaVault

USO:
  ./omv-manager.sh [opção]

OPÇÕES:
  (sem opções)  Executa todas as operações configuradas
  --dry-run     Mostra o que será feito sem executar
  --status      Mostra status atual do sistema
  --help        Mostra esta mensagem

FLUXO:
  1. Cria grupos no Linux e registra no OMV
  2. Cria usuários no Linux e registra no OMV
  3. Cria pastas no disco de dados
  4. Configura ACLs nas pastas
  5. Registra shared folders no OMV
  6. Cria compartilhamentos SMB
  7. Aplica configuração com omv-salt

CONFIGURAÇÃO:
  Edite as variáveis USERS, GROUP_LIST, FOLDERS, FOLDER_ACLS e
  SMB_SHARES no início do script antes de executar.

EXEMPLOS:
  ./omv-manager.sh              # Executa tudo
  ./omv-manager.sh --dry-run    # Simulação
  ./omv-manager.sh --status     # Ver status atual
EOF
}

# ============================================================================
# MAIN
# ============================================================================

main() {
  case "${1:-}" in
    --help|-h)
      show_help
      exit 0
      ;;
    --status)
      show_status
      exit 0
      ;;
    --dry-run)
      DRY_RUN=true
      log_info "MODO DRY-RUN - Nenhuma alteração será feita"
      ;;
    "")
      log_info "Iniciando gerenciamento OMV..."
      echo "========================================"
      ;;
    *)
      log_error "Opção desconhecida: $1"
      show_help
      exit 1
      ;;
  esac

  if [ "$DRY_RUN" = false ] && [ "$(id -u)" -ne 0 ]; then
    log_error "Este script precisa ser executado como root"
    exit 1
  fi

  for cmd in jq xmlstarlet; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      log_error "Comando '$cmd' não encontrado."
      exit 1
    fi
  done

  echo ""
  create_groups
  create_omv_groups

  echo ""
  create_users
  create_omv_users

  echo ""
  create_folders

  echo ""
  set_acls

  echo ""
  create_omv_sharedfolders

  echo ""
  create_smb_shares

  echo ""
  if [ "$DRY_RUN" = false ]; then
    apply_config
  else
    log_info "DRY-RUN: omv-salt deploy run samba (pulado)"
  fi

  echo ""
  log_info "Gerenciamento concluído com sucesso!"
  echo "========================================"
}

main "$@"
