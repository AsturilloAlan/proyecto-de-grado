# Sistema de Apoyo a la Auditoría Externa

Proyecto de grado: sistema web que identifica transacciones atípicas en registros contables
digitales con reglas de validación y Machine Learning, para ST&S Auditores y Consultores S.R.L.

## Stack

- Backend: Python + Django 4.2
- Análisis de datos / ML: pandas y scikit-learn (One-Class SVM, elegido frente a Isolation Forest y LOF)
- Base de datos: MySQL Server
- Interfaz: Bootstrap 5.3 y Chart.js (copias locales en `static/vendor`)
- Reportes: ReportLab (PDF) y openpyxl (Excel)

## Estructura

- `usuarios/`: inicio de sesión con verificación en dos pasos y JWT, roles, perfil, recuperación de contraseña y gestión de usuarios
- `registros/`: empresas, gestiones, carga y validación de libros contables, revisión de avisos y de atípicas, historial de cambios
- `analisis/`: variables del modelo, evaluación comparativa, configuración, entrenamiento y aplicación de One-Class SVM
- `reportes/`: reportes de atípicas, hallazgos y observaciones, gráficos por gestión, comparación entre gestiones y exportación a PDF y Excel

## Puesta en marcha local

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Crear en MySQL la base `auditoria_db` (cotejamiento `utf8mb4_general_ci`) y copiar `.env.example`
como `.env` con los datos de conexión, las claves y el correo.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py descargar_estaticos   # Bootstrap local, para usar el sistema sin internet
python manage.py runserver
```

El sistema queda en `http://127.0.0.1:8000/`.

## Estado actual

- [x] Autenticación con roles, bloqueo por intentos, verificación en dos pasos por correo y JWT
- [x] Empresas clientes y gestiones, con historial de cambios
- [x] Carga y validación de registros contables (Excel, CSV y PDF del Libro Diario), en segundo plano
- [x] Revisión de avisos y de atípicas por el auditor, con justificación
- [x] Evaluación comparativa de los tres algoritmos, configuración y entrenamiento de One-Class SVM
- [x] Análisis de cada carga con el modelo y explicación de los motivos
- [x] Reportes de atípicas, hallazgos y observaciones, gráficos y exportación a PDF y Excel
- [x] Recuperación de contraseña, perfil del usuario y bitácora de correos

## Comandos útiles

- `python manage.py test`: pruebas automatizadas de todo el sistema.
- `python manage.py evaluar_modelos <id_carga> ...`: evaluación comparativa de los algoritmos.
- `python manage.py configurar_modelo <id_2022> <id_2023>`: elige la configuración de One-Class SVM.
- `python manage.py entrenar_modelo <id_2022> <id_2023>`: entrena y guarda el modelo en `modelos_ml/`.
- `python manage.py puntuar_carga <id_carga>`: aplica el modelo guardado a una carga.
- `python manage.py resumen_validacion`: precisión de las atípicas revisadas por el auditor.
- `python manage.py medir_rendimiento --carga <id>`: mide los tiempos del flujo sin guardar datos.
- `python manage.py exportar_dataset <id_carga>`: dataset de una carga en CSV.
- `python manage.py reclasificar_cuentas`: corrige el tipo de cuenta según su código.
- `python manage.py revisar_base_datos`: busca tablas o migraciones que ya no corresponden al código (solo informa).
- `python manage.py borrar_datos_prueba`: borra los datos de negocio sin tocar usuarios (pide `--confirmar`).
