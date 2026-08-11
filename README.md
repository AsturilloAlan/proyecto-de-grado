# Sistema de Apoyo a la Auditoría Externa

Proyecto de grado — sistema web que aplica Machine Learning (Isolation Forest) para identificar transacciones atípicas en registros contables digitales, aplicado al caso de ST&S Auditores y Consultores S.R.L.

## Stack

- Backend: Python + Django
- Análisis de datos / ML: pandas, scikit-learn (Isolation Forest)
- Base de datos: SQLite en desarrollo (se evaluará PostgreSQL para volúmenes reales)

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

# 3. Aplicar migraciones
python manage.py migrate

# 4. Crear un usuario administrador (para poder iniciar sesión)
python manage.py createsuperuser

# 5. Correr el servidor
python manage.py runserver
```

Luego abre `http://127.0.0.1:8000/` e inicia sesión con el usuario creado en el paso 4.

## Estado actual

- [x] Esqueleto del proyecto (Iteración 1, en curso)
- [x] Autenticación de usuarios (login/logout)
- [ ] Carga y validación de registros contables
- [ ] Detección de anomalías (Isolation Forest)
- [ ] Reportes y visualización
