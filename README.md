# OMV Manager

Gerenciamento automatizado de usuários, grupos, pastas, ACLs e compartilhamentos SMB no **OpenMediaVault**.

## Requisitos

- Python 3.8+
- Servidor OMV 8.x com acesso SSH (root)
- Dependências Python:

```bash
pip install -r requirements.txt
```

## Instalação

```bash
git clone <repo> omv-manager
cd omv-manager
pip install -r requirements.txt
```

Edite o arquivo `config.yaml` com os dados do seu servidor e a configuração desejada.

## Uso

### Interface gráfica

```bash
python omv-gui.py
```

### Linha de comando

```bash
# Aplicar configuração
python omv-manager.py --config config.yaml --apply

# Simular sem alterar
python omv-manager.py --config config.yaml --dry-run

# Ver status do servidor
python omv-manager.py --config config.yaml --status
```

## Estrutura

```
omv-manager/
├── omv-manager.py      # CLI principal
├── omv-gui.py          # Interface gráfica (Tkinter)
├── config.yaml         # Configuração YAML
├── requirements.txt    # Dependências
└── README.md
```

## Configuração

Edite `config.yaml` para definir:

- **server** — host, porta e senha SSH
- **settings** — base path e mntentref do disco
- **users** — nome, senha, grupo primário, grupos adicionais, email
- **groups** — nome, comentário, membros
- **folders** — nome, grupo proprietário, permissão (ex: 770)
- **acls** — pasta, tipo (user/group), alvo, permissão (rwx, rx, etc)
- **smb_shares** — nome, comentário, guest, browseable, inherit_acl

## Funcionalidades

- Cria grupos no sistema e registra no OMV
- Cria usuários no sistema, define senha e senha Samba
- Cria pastas no disco com permissões e setgid
- Configura ACLs (usuários e grupos adicionais)
- Registra shared folders no OMV
- Cria compartilhamentos SMB
- Aplica com `omv-salt deploy run samba`
- 100% idempotente — pode executar múltiplas vezes sem duplicar entradas
- Funciona no Windows e Linux (Python puro)
