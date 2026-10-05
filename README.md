# Sistema de Apoyo a la Auditoría Externa

Proyecto de grado: sistema web que identifica transacciones atípicas en registros contables
digitales con reglas de validación y Machine Learning, para ST&S Auditores y Consultores S.R.L.

## Stack

- Backend: Python + Django 4.2
- Análisis de datos / ML: pandas, scikit-learn (One-Class SVM, elegido frente a Isolation Forest y LOF)
- Base de datos: MySQL Server

## Estructura

- `usuarios/`: autenticación con roles y verificación en dos pasos (RF-07)
- `registros/`: carga, validación y revisión de registros contables (RF-01, RF-02)
- `analisis/`: variables y evaluación de los modelos de detección de anomalías (RF-03, RF-04)
- `reportes/`: reportes y exportación (RF-05, RF-06, RF-08), previsto para la Iteración 3

## Puesta en marcha local

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Crear en MySQL la base `auditoria_db` (cotejamiento `utf8mb4_general_ci`) y copiar `.env.example`
como `.env` con los datos de conexión (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`).

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py descargar_estaticos   # Bootstrap local, para usar el sistema sin internet
python manage.py runserver
```

El sistema queda en `http://127.0.0.1:8000/`.

## Estado actual

- [x] Autenticación con roles, bloqueo por intentos y verificación en dos pasos por correo
- [x] Empresas clientes y gestiones, con historial de cambios
- [x] Carga y validación de registros contables (Excel, CSV y PDF del Libro Diario), en segundo plano
- [x] Revisión de avisos por el auditor (válido u observado) y reporte de observaciones
- [x] Evaluación comparativa de los tres algoritmos (`python manage.py evaluar_modelos`)
- [ ] Entrenamiento del modelo elegido e integración en el sistema web
- [ ] Visualización y exportación de resultados del análisis

## Comandos útiles

- `python manage.py test usuarios registros`: pruebas automatizadas.
- `python manage.py evaluar_modelos <id_carga> ...`: evaluación comparativa de los algoritmos.
- `python manage.py exportar_dataset <id_carga>`: dataset de una carga en CSV.
- `python manage.py reclasificar_cuentas`: corrige el tipo de cuenta según su código.
- `python manage.py revisar_base_datos`: busca tablas o migraciones que ya no corresponden al código (solo informa).
- `python manage.py borrar_datos_prueba`: borra los datos de negocio sin tocar usuarios (pide `--confirmar`).
