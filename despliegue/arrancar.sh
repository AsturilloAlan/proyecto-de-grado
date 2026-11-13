#!/bin/bash
APP=/home4/smartupsrl/apps/anomalias
PORT=8742
curl -s -o /dev/null --max-time 10 http://127.0.0.1:$PORT/ && exit 0
cd $APP
setsid nohup $APP/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:$PORT --workers 1 --threads 4 --timeout 300 >> $APP/gunicorn.log 2>&1 < /dev/null &
