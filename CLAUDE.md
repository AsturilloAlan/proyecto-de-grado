# Sistema de apoyo a la auditoría externa (ST&S Auditores y Consultores S.R.L.)

Proyecto de grado de Alan Jhonatan Asturillo Sangalli (UNIVALLE, Ingeniería de Sistemas).
Tutora: Ing. María Conde Altamirano. Identifica transacciones atípicas en registros
contables de las empresas auditadas por ST&S, con reglas de validación y Machine Learning.

## Stack

- Django 4.2, MySQL (XAMPP) vía PyMySQL; SQLite solo para pruebas.
- Bootstrap 5.3 + Bootstrap Icons (copia local en `static/vendor`, respaldo por CDN). CSS y JS
  van dentro de las plantillas (`templates/base.html` concentra estilos globales).
- pandas, openpyxl/xlrd, pdfplumber para leer libros; scikit-learn y matplotlib para el modelo.
- Apps: `usuarios` (login con 2FA por correo, roles Administrador/Auditor, perfil),
  `registros` (empresas, gestiones, cargas, validación, avisos, reporte), `analisis` (modelos ML).

## Comandos

```
python manage.py runserver
python manage.py test usuarios registros          # pruebas (procesan en el momento, sin hilo)
python manage.py migrate
python manage.py descargar_estaticos               # Bootstrap local para uso sin internet
python manage.py reclasificar_cuentas [--confirmar --usuario <usuario>]
python manage.py evaluar_modelos <id_carga> ...    # evaluación comparativa IF / LOF / OCSVM
python manage.py exportar_dataset <id_carga>
python manage.py borrar_datos_prueba [--confirmar --conservar-empresas --archivos]  # datos de prueba, no usuarios
```

## Conceptos del dominio

- **Carga**: un archivo (Excel, CSV o PDF de libro mayor o diario) de una empresa y gestión.
  Se procesa en segundo plano (`registros/procesamiento.py`); mientras tanto `estado="pendiente"`.
- **Error**: fila que no se guarda (fecha vacía, Debe y Haber a la vez, etc.).
- **Aviso**: fila guardada que el auditor debe revisar (duplicado, naturaleza de cuenta, fecha
  fuera de gestión, cuenta nueva). El auditor la marca **válido** u **observado** con justificación.
- **Observado** = hallazgo confirmado por el auditor; va al reporte de observaciones.
- El **cliente** de ST&S es la empresa auditada; quien responde es **el contador de la empresa**.
- Plan de cuentas **por empresa** (`CuentaContable.empresa`); el tipo se deduce del primer dígito
  del código (1 activo … 5 gasto).
- Si más de la mitad de las fechas cae fuera de la gestión, la carga se rechaza (gestión equivocada).
- Modelo elegido en la evaluación (actividad 10): **One-Class SVM** (mejor AUC-PR en 2022 y 2023).
  Es no supervisado: se entrena (`fit`) sin etiquetas.

## Reglas de redacción (interfaz y tesis)

- Español neutro en **tercera persona** o impersonal. **Nunca voseo** ("corregí", "guardá") ni tuteo
  imperativo ("prueba de nuevo" → "Intentar de nuevo").
- Textos breves. Sin atajos de teclado ni pistas tipo "Ctrl + Enter".
- Las justificaciones frecuentes (`registros/frases.py`) describen lo comprobado, no acciones
  pendientes; el servidor rechaza una frase que contradiga la decisión.
- En la tesis: sin raya (—), sin conectores de IA ("En este sentido", "Finalmente", "no solo… sino
  también"), cifras concretas en vez de "varios" o "grandes volúmenes".

## Reglas de trabajo

- No subir ni mostrar `.env`, `media/`, `db.sqlite3` ni carpetas `evaluacion_modelos_*`.
- No borrar datos: las cargas se **anulan** con motivo, no se eliminan. Toda decisión queda en
  `HistorialCambio`.
- Cambios que afecten objetivos, variables o el hilo conductor de la tesis: consultar con la tutora.
- Avisar si algo excede el alcance (límites: gestiones 2022, 2023 y 2025; 1.300 a 24.000
  registros por gestión; el sistema apoya, no reemplaza, el criterio del auditor).
- Después de cambiar código: correr las pruebas; en plantillas, revisar también la vista de celular.
- Elementos `position: fixed` dentro de la tarjeta principal: moverlos a `<body>` o evitar
  `transform` en ancestros.
