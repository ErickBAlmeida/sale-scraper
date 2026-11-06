# sale_scraper — Bot de Alerta de Promoções (Telegram)

Lê ofertas de canais/grupos públicos do Telegram e notifica cada usuário sobre
os produtos que ele cadastrou por palavra-chave.

## Arquitetura (resumo)

Dois componentes no mesmo processo Python/asyncio:

| Componente | Lib | Papel |
|---|---|---|
| **Listeners (contas dos usuários)** | Telethon (MTProto) | Um por usuário: lê os canais que **ele** cadastrou, **com a conta dele** |
| **Notifier (bot)** | python-telegram-bot | Comandos, login das contas e envio dos alertas |

> ⚠️ O Bot API **não lê** canais públicos de terceiros. Por isso cada usuário
> conecta a **própria conta** do Telegram (`/login`) e o bot lê os canais com ela.
> Contas automatizadas ficam numa área cinzenta dos ToS do Telegram — cada
> usuário assume esse risco para a própria conta.

```
bot/        handlers de comando (PTB) + login.py (/login, /logout)
listener/   manager.py (uma conexão por conta) + userbot.py (listener de uma conta)
core/       normalização, matching, preço, validação, tempo, criptografia
db/         modelos + repositório (SQLAlchemy/SQLite) + migrações automáticas
config.py   carrega .env
main.py     sobe bot + contas juntos
tests/      testes (pytest)
```

## Pré-requisitos

1. **Token do bot**: fale com o [@BotFather](https://t.me/BotFather) → `/newbot` → copie o token.
2. **API do Telegram**: acesse <https://my.telegram.org> → *API development tools* → copie `api_id` e `api_hash`
   (um único app serve para todas as contas dos usuários).
3. **Seu id**: fale com o [@userinfobot](https://t.me/userinfobot) para obter o `OWNER_ID`.
4. **Chave das sessões** (`SESSION_KEY`), que criptografa as sessões salvas:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   ⚠️ Guarde bem: se perder ou trocar a chave, todos os usuários precisam refazer `/login`.
5. Copie `.env.example` para `.env` e preencha os valores.

## Rodando

Não há mais login no terminal: todo mundo, inclusive o admin, conecta a conta pelo
`/login` dentro do bot.

**Local:**
```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Linux/Mac:           source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Docker:**
```bash
docker compose up -d --build
docker compose logs -f
```

O volume `./data` persiste banco SQLite (com as sessões criptografadas) e logs.
`restart: "no"`: o container **não** inicia sozinho (nem após reboot ou crash).
Suba manualmente com `docker compose up -d` ou pelo ▶ no Docker Desktop.
Para rodar 24/7 sem supervisão, troque para `restart: unless-stopped`.

**Migração de instalações antigas:** na primeira execução desta versão, os canais
que eram globais passam a ser do admin, e o arquivo `data/userbot.session` (se existir)
vira a conta do admin automaticamente — sem precisar refazer o login.

## Uso

0. **Acesso:** o bot é fechado. Quem manda `/start` fica *aguardando* e o admin recebe
   uma mensagem com **✅ Aprovar / ❌ Recusar**. Só aprovados usam os comandos (inclusive o `/login`).
1. `/login` — toque em **📱 Compartilhar meu número**, receba o código no
   Telegram e digite-o **com espaços** (`1 2 3 4 5`; sem espaços o Telegram bloqueia o código).
   Se a conta tiver verificação em duas etapas, o bot pede a senha. As mensagens com
   código e senha são apagadas na hora.
2. `/source_add @canal` — monitora um canal público (a sua conta entra nele, se ainda não segue).
3. `/add rtx 4070` ou `/add ryzen 5 5500 <500` (com preço máximo).

**Demais comandos:** `/list`, `/remove 2`, `/clear`, `/pause`, `/resume`, `/settings`,
`/sources`, `/source_remove @canal`, `/stats`, `/logout`, `/help`.
Sem conta conectada, só `/start`, `/help` e `/login` funcionam.

`/source_remove` só faz a sua conta sair do canal se foi o bot que entrou nele;
canais que você já seguia continuam seguidos.

**Admin (`OWNER_ID`):** `/status` (saúde do bot e das contas), `/pendentes` (pedidos de
acesso), `/revogar_acesso <id|@user>` (encerra a sessão e apaga os dados) e `/broadcast <msg>`.

**Desconectar:** `/logout` encerra a sessão no Telegram. Também dá para encerrar em
Telegram > Configurações > Dispositivos — o bot detecta em até 5 min e avisa o usuário.

**Privacidade:** `/privacidade` explica o que é guardado; `/apagar_dados` apaga tudo do
usuário (sessão, canais, palavras, histórico) e sai dos canais em que o bot o fez entrar.
Quem bloqueia o bot tem a sessão encerrada e apagada automaticamente.

## Segurança e termos do Telegram

- App **não oficial**: o `/start`, o `/login` e o `/privacidade` informam que ele usa a
  API do Telegram e não tem vínculo com o Telegram (termos da API, item 2.2).
- Acesso só com aprovação do admin — reduz o risco de o fluxo de login (que pede o
  código, como golpes fazem) ser denunciado por desconhecidos.
- Sessões das contas ficam criptografadas (Fernet) no banco; a chave fica só no `.env`.
  Quem tiver `.env` + `data/` tem acesso às contas conectadas: proteja os dois.
- Telefone, código, senha e sessão nunca vão para o log.
- O bot só aceita conectar o **próprio** número de quem pediu o `/login`.
- Anti-spam: cada conta entra em no máximo `JOINS_PER_HOUR` canais novos por hora.
- `MAX_ACCOUNTS` e `MAX_SOURCES` no `.env` limitam contas conectadas e canais por usuário.

## Testes

```bash
pip install pytest
pytest -q
```

## Infra sugerida (grátis/baixo custo)

**Oracle Cloud — Always Free VM** (ARM, nunca dorme, disco persistente) rodando
o `docker compose`. Alternativas: Raspberry Pi em casa, Fly.io, VPS Hetzner/Contabo.
Faça backup periódico de `data/*.sqlite` **e** do `.env` (sem a `SESSION_KEY` o backup não serve).
