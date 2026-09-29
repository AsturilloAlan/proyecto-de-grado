# Sistema de Apoyo a la Auditoría Externa

Proyecto de grado — sistema web que aplica Machine Learning (Isolation Forest) para identificar transacciones atípicas en registros contables digitales, aplicado al caso de ST&S Auditores y Consultores S.R.L.

## Stack

- Backend: Python + Django
- Análisis de datos / ML: pandas, scikit-learn (Isolation Forest)
- Base de datos: MySQL Server (instalación estándar con MySQL Workbench)

## Estructura

- `usuarios/` — autenticación (RF-07)
- `registros/` — carga y validación de registros contables (RF-01, RF-02)
- `analisis/` — caracterización de variables y detección de anomalías (RF-03, RF-04)
- `reportes/` — generación, visualización y exportación de reportes (RF-05, RF-06, RF-08)

## Cómo correr el proyecto localmente

```bash
# 1. Crear y activar entorno virtual
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 2. Instalar dependencias
pip install -r requirements.txt
```

**3. Crear la base de datos en MySQL Server (con MySQL Workbench):**

1. Abre MySQL Workbench y conéctate a tu servidor local (en este proyecto: `127.0.0.1`, puerto `3307`, usuario `root`).
2. En el panel de "Schemas", clic derecho → **Create Schema** → nombre `auditoria_db` → cotejamiento `utf8mb4_general_ci` → Apply.

Por defecto el proyecto se conecta con usuario `root`, la contraseña configurada de esta instalación, host `127.0.0.1` y puerto `3307`. Si tu MySQL tiene otra configuración, define estas variables de entorno antes de correr los comandos: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

```bash
# 4. Aplicar migraciones (con MySQL Server ya corriendo)
python manage.py migrate

# 5. Crear un usuario administrador (para poder iniciar sesión)
python manage.py createsuperuser

# 6. Correr el servidor
python manage.py runserver
```

Luego abre `http://127.0.0.1:8000/` e inicia sesión con el usuario creado en el paso 5.

## Estado actual

- [x] Autenticación con roles, bloqueo por intentos y verificación en dos pasos por correo
- [x] Gestión de empresas clientes y gestiones, con historial de cambios
- [x] Carga y validación de registros contables (Excel, CSV y PDF del Libro Diario)
- [x] Revisión de avisos por el auditor (válido u observado) y reporte de observaciones
- [x] Prototipo de variables y de los tres algoritmos (`analisis/prototipo_modelos.py`), fuera del sistema web
- [ ] Evaluación y elección del algoritmo
- [ ] Integración del modelo en el sistema web
- [ ] Visualización y exportación de resultados del análisis

## Comandos útiles

- `python manage.py test usuarios registros`: corre las pruebas automatizadas.
- `python manage.py revisar_base_datos`: busca tablas o migraciones que ya no corresponden al código (solo informa).
- `python manage.py borrar_datos_prueba`: borra cargas, empresas y gestiones de prueba sin tocar usuarios (pide `--confirmar`).
