#!/bin/bash
set -e
H=/home4/smartupsrl
APP=$H/apps/anomalias
export HOME=$H
export UV_PYTHON_INSTALL_DIR=$H/apps/python
export UV_CACHE_DIR=$H/apps/.uvcache
echo "== uv"
python3 -m pip install --quiet --target $H/apps/uvpkg uv
UV=$H/apps/uvpkg/bin/uv
echo "== python 3.13"
$UV python install 3.13
echo "== entorno"
[ -d $APP/venv ] || $UV venv --python 3.13 $APP/venv
echo "== librerias"
$UV pip install --python $APP/venv/bin/python -r $APP/requirements-servidor.txt
cd $APP
echo "== verificacion"
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py check --deploy
venv/bin/python manage.py showmigrations | grep -c "\[ \]" || true
echo "== LISTO"
