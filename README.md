# sale_scraper — Bot de Alerta de Promoções (Telegram)

Lê ofertas de canais/grupos públicos do Telegram e notifica cada usuário sobre
os produtos que ele cadastrou por palavra-chave.

## Arquitetura (resumo)

Dois componentes no mesmo processo Python/asyncio:

| Componente | Lib | Papel |
|---|---|---|
| **Listener (userbot)** | Telethon (MTProto) | Entra nos canais-fonte e captura cada nova mensagem |
| **Notifier (bot)** | python-telegram-bot | Comandos do usuário + envio dos alertas |

> ⚠️ O Bot API **não lê** canais públicos de terceiros. Por isso o listener usa
> uma **conta de usuário** (userbot). Use um número dedicado e sem spam
> (área cinzenta dos ToS do Telegram).

```
bot/        handlers de comando (PTB)
listener/   userbot Telethon + dispatch de alertas
core/       normalização, matching, validação, tempo
db/         modelos + repositório (SQLAlchemy/SQLite)
config.py   carrega .env
main.py     sobe listener + bot juntos
tests/      testes do core
```

## Pré-requisitos

1. **Token do bot**: fale com o [@BotFather](https://t.me/BotFather) → `/newbot` → copie o token.
2. **API do userbot**: acesse <https://my.telegram.org> → *API development tools* → copie `api_id` e `api_hash`.
3. **Seu id**: fale com o [@userinfobot](https://t.me/userinfobot) para obter o `OWNER_ID`.
4. Copie `.env.example` para `.env` e preencha os valores.

## Rodando localmente (primeira vez — autenticação do userbot)

A primeira execução pede o **telefone e o código SMS** da conta do userbot para
criar o arquivo de sessão (`data/userbot.session`). Faça isso num terminal
interativo:

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# Linux/Mac:           source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Depois do primeiro login, a sessão fica salva e não pede mais código.

## Rodando com Docker (produção 24/7)

Autentique uma vez interativamente (para gerar a sessão) e depois suba em background:

```bash
# 1ª vez — interativo, para digitar telefone/código:
docker compose run --rm bot python main.py
# Ctrl+C depois do "Bot e listener em execução."

# Produção:
docker compose up -d
docker compose logs -f
```

O volume `./data` persiste sessão, banco SQLite e logs entre reinícios.
`restart: unless-stopped` garante que volte após crash/reboot.

## Uso

**Usuário:**
`/start`, `/add rtx 4070`, `/list`, `/remove 2`, `/clear`, `/pause`, `/resume`,
`/settings`, `/sources`, `/stats`, `/help`.

**Admin (`OWNER_ID`):**
`/source_add @canal` — passa a monitorar o canal (o userbot entra nele).
`/source_remove @canal` — para de monitorar.
`/broadcast <msg>` — avisa todos os usuários.

## Testes

```bash
pip install pytest
pytest -q
```

## Infra sugerida (grátis/baixo custo)

**Oracle Cloud — Always Free VM** (ARM, nunca dorme, disco persistente) rodando
o `docker compose`. Alternativas: Raspberry Pi em casa, Fly.io, VPS Hetzner/Contabo.
Faça backup periódico de `data/*.sqlite` e `data/*.session`.
