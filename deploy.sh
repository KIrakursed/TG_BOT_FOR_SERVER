#!/bin/bash
# Production update: fetch -> deps -> tests -> lint -> migrate -> drift check -> restart.
# Aborts before restart if tests, lint, migration or schema drift fail.
set -e
cd /home/ubuntu/telegram_bot_krisha.kz
git pull --ff-only
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/python -m pytest -q -W ignore::DeprecationWarning
.venv/bin/python -m ruff check app tests
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic check
sudo systemctl restart kvartira-bot
sleep 5
sudo systemctl is-active kvartira-bot
sudo systemctl status kvartira-bot --no-pager | head -8
journalctl -u kvartira-bot -n 15 --no-pager
echo "DEPLOY_OK"
