# agentvpnservice

Агент починки флота Hiddify: принимает инциденты от мониторинга (hidsal), через Portix выбирает **готовый** сценарий, спрашивает админа в Telegram и выполняет только после «Фиксить».

- Хост: `192.168.1.152` → `/ssd/www/agentvpnservice`
- URL: https://agentvpnservice.1tlt.ru
- Модель: OpenAI-compatible Portix (`PORTIX_MODEL`, ключ из консоли)

## Безопасность

- Модель **не** получает SSH и **не** пишет shell-команды.
- Исполняются только allowlist-сценарии из `app/runbooks/`.
- Секреты только в `.env` на хосте (не в git).
- По умолчанию `DRY_RUN=1`.

## API для hidsal

`POST /api/v1/incidents` + `Authorization: Bearer <INCIDENT_WEBHOOK_TOKEN>`

См. `examples/hidsal-post.sh`.

## Выкладка

```bash
cd /ssd/www/agentvpnservice
git pull
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env   # заполнить
systemctl restart agentvpnservice
```

Systemd unit: `deploy/agentvpnservice.service`  
Apache (этот хост): `deploy/apache-agentvpnservice.conf` + `deploy/apache-agentvpnservice-le-ssl.conf`  
Nginx-заготовка (если перенос): `deploy/nginx-agentvpnservice.conf`
