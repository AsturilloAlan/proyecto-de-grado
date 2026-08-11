# Sistema de Apoyo a la Auditoría Externa

Proyecto de grado — sistema web que aplica Machine Learning (Isolation Forest) para identificar transacciones atípicas en registros contables digitales, aplicado al caso de ST&S Auditores y Consultores S.R.L.

## Stack

- Backend: Python + Django
- Análisis de datos / ML: pandas, scikit-learn (Isolation Forest)
- Base de datos: MySQL (vía XAMPP en desarrollo local)

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

**3. Crear la base de datos en MySQL (XAMPP):**

1. Abre el Panel de Control de XAMPP y arranca el módulo **MySQL** (no necesitas Apache).
2. Entra a phpMyAdmin: `http://localhost/phpmyadmin`.
3. Pestaña **Bases de datos** → nombre `auditoria_db` → cotejamiento `utf8mb4_general_ci` → Crear.

Por defecto el proyecto usa usuario `root` sin contraseña (lo que trae XAMPP de fábrica). Si tu MySQL tiene otra configuración, define estas variables de entorno antes de correr los comandos: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

```bash
# 4. Aplicar migraciones (con MySQL de XAMPP ya corriendo)
python manage.py migrate

# 5. Crear un usuario administrador (para poder iniciar sesión)
python manage.py createsuperuser

# 6. Correr el servidor
python manage.py runserver
```

Luego abre `http://127.0.0.1:8000/` e inicia sesión con el usuario creado en el paso 5.

## Estado actual

- [x] Esqueleto del proyecto (Iteración 1, en curso)
- [x] Autenticación de usuarios (login/logout)
- [ ] Carga y validación de registros contables
- [ ] Detección de anomalías (Isolation Forest)
- [ ] Reportes y visualización
