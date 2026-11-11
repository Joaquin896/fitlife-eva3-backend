# Registro de apoyo de Inteligencia Artificial

**Proyecto:** FitLife – Plataforma de Control de Asistencia y Reserva de Clases
**Asignatura:** Desarrollo Backend – Evaluación Parcial N°3
**Herramienta utilizada:** Claude (Anthropic), modo agente con acceso a terminal y navegador
**Fecha:** 11-11-2026

---

## 1. Consulta inicial

Se entregaron a la IA dos documentos: el enunciado del Proyecto 2 (FitLife) y la pauta de la EVA 3 con su rúbrica.

> **Prompt:** "ok claude, ayúdame a hacer todo lo que pide en las rúbricas, levanta la máquina en AWS igual, y dame un guion de cómo probar que funciona todo correctamente"

Aclaraciones respondidas a la IA antes de comenzar:
- El repositorio se sube a mi cuenta de GitHub desde el navegador.
- El entorno de AWS es **AWS Academy Learner Lab**.

## 2. Apoyos solicitados y resultado

| # | Apoyo solicitado | Qué entregó la IA | Criterio de rúbrica |
|---|---|---|---|
| 1 | Análisis de la rúbrica y del caso FitLife | Desglose de los 4 requerimientos del caso en modelos, reglas de negocio y pantallas | Todos |
| 2 | Diseño del modelo de datos relacional | Modelos `Plan`, `Socio`, `Membresia`, `Pago`, `Clase`, `Sesion`, `Reserva`, `RegistroAcceso`, `Instalacion` con FK, `UniqueConstraint` parcial (una reserva vigente por socio y sesión), `CheckConstraint` (fin ≥ inicio) e índice | 1 |
| 3 | Configuración de PostgreSQL | `settings.py` leyendo credenciales desde `.env`, driver `psycopg 3`, migraciones del ORM | 1 |
| 4 | Personalización del Django Admin | `list_display`, `list_filter` (incluye filtro propio por estado de membresía), `search_fields`, `date_hierarchy`, inlines, acciones (congelar, descongelar, cancelar con promoción de lista de espera, regenerar QR), estados con colores, vista del QR, bitácora de solo lectura y template `admin/base_site.html` con la marca FitLife | 2 |
| 5 | Lógica de lista de espera sin sobrecupo | Servicio `reservar()` con `select_for_update()` y `promover_lista_espera()` por orden de llegada | 3 |
| 6 | Validación de acceso por QR de baja latencia | UUID por socio, QR en SVG con la librería `qrcode`, servicio `validar_acceso()` que mide la latencia con `time.perf_counter()`, anti-passback y control de aforo | 3 |
| 7 | Reglas de membresía | Cálculo automático de `fecha_fin`, paso a vencida, congelar/descongelar extendiendo vigencia, bloqueo por pago pendiente, no superposición | 3 |
| 8 | API REST con DRF | ViewSets, serializers que reutilizan `Model.clean()`, acciones personalizadas (`reservar`, `cancelar`, `congelar`), endpoints de aforo y validación | 3, 4 |
| 9 | Seguridad y roles | Grupos *Recepcion* y *Socio*, permisos DRF propios, mixins para vistas HTML, CSRF en `fetch`, configuración de sesión y cookies | 4 |
| 10 | Frontend responsivo | Templates con Bootstrap 5, dashboard que se refresca cada 3 s con `fetch`, pantalla de torniquete, reservas asíncronas, vista "Mi cuenta" con QR | 3, 4 |
| 11 | Pruebas automáticas | 20 tests (`gym/tests.py`) sobre cupos, lista de espera, congelamiento, accesos, aforo, permisos por rol y CRUD por API | 3 |
| 12 | Script de despliegue en EC2 | `deploy/setup_ec2.sh`: PostgreSQL, entorno virtual, migraciones, `collectstatic`, datos demo, servicio systemd para Gunicorn, Nginx en el puerto 80 y tareas `cron` | 5 |
| 13 | Despliegue en AWS Academy | Creación del Security Group, la instancia EC2 Ubuntu 24.04 con *User data* y la Elastic IP desde la consola web | 5 |
| 14 | Guion de pruebas para la defensa | Paso a paso para demostrar cada criterio en la IP pública y en el Admin | 5 |

## 3. Validación del resultado

- `python manage.py test gym` → **20 pruebas OK** sobre PostgreSQL.
- Prueba de punta a punta en navegador (local): login por rol, escaneo de los 6 socios demo (permitidos y denegados con su motivo), reserva hasta llenar el cupo, ingreso a lista de espera, cancelación con promoción automática, socio sin permiso al dashboard (403), vista móvil.
- Verificación en la EC2 del health-check `/api/salud/` mostrando la versión de PostgreSQL.

## 4. Decisiones tomadas con apoyo de la IA

- **Toda la lógica de negocio en `services.py`** para que la API, las vistas HTML, el Admin y los tests usen las mismas reglas.
- **Bootstrap 5.3.3 desde CDN (jsDelivr)** con versión fija para mantener el repositorio liviano.
- **`ALLOWED_HOSTS=*`** porque en AWS Academy la IP pública cambia al reiniciar el laboratorio (se mitiga con Elastic IP).
- La cámara del navegador exige HTTPS, por eso el acceso se valida con lector USB (modo teclado) o con la simulación de escaneo.

## 5. Consultas posteriores

_(Agregar aquí cualquier consulta adicional realizada a la IA durante la preparación de la defensa.)_
