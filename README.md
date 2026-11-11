# FitLife · Control de Asistencia y Reserva de Clases

Evaluación Parcial N°3 – Desarrollo Backend (INACAP) · Proyecto 2 (FitLife)
Estudiante: Joaquín Figueroa · Docente: Marcelo Alvarado Aravena

Aplicación web fullstack con **Django 5.2 + Django REST Framework + PostgreSQL**, desplegada en **una instancia AWS EC2** (AWS Academy) con Nginx + Gunicorn.

## 🌐 Aplicación desplegada

| | URL |
|---|---|
| Aplicación | **http://IP_PUBLICA_EC2/** |
| Django Admin | http://IP_PUBLICA_EC2/admin/ |
| API REST (navegable) | http://IP_PUBLICA_EC2/api/ |
| Health-check | http://IP_PUBLICA_EC2/api/salud/ |

> La instancia vive en AWS Academy Learner Lab: solo está encendida mientras el laboratorio está iniciado.

### Usuarios de demostración

| Usuario | Rol | Escenario |
|---|---|---|
| `admin` | Superusuario | Django Admin completo (contraseña entregada en la defensa) |
| `recepcion` | Recepción | Control QR, dashboard de aforo, CRUD de socios, planes, membresías, pagos, clases |
| `ana`, `bruno`, `felipe` | Socio | Membresía **activa** y pagada |
| `carla` | Socio | Membresía **vencida** |
| `diego` | Socio | Membresía **congelada** |
| `elena` | Socio | Membresía activa con **pago pendiente** |

Contraseña de los usuarios demo (excepto admin): `FitLife2026!`

## ✅ Requerimientos del caso cubiertos

1. **Planes, pagos y estados de membresía (Activa, Vencida, Congelada)**
   CRUD completo en Django Admin, en las vistas HTML de *Gestión* y en la API (`/api/planes/`, `/api/membresias/`, `/api/pagos/`).
   - `fecha_fin` se calcula con la duración del plan; no se permiten membresías superpuestas.
   - Paso automático a **Vencida** (al consultar y por `cron` diario).
   - **Congelar / descongelar**: al descongelar se extiende la fecha de término en los días congelados.
   - Una membresía solo habilita acceso si está **pagada** (suma de pagos `PAGADO` ≥ precio del plan).
2. **Validación de acceso por QR**
   Cada socio tiene un `UUID` único codificado en su QR (`/socios/<id>/qr.svg`). La pantalla *Control QR* acepta lectores USB (actúan como teclado) o la simulación con botones. La API `POST /api/acceso/validar/` responde en pocos milisegundos (se guarda la latencia) y registra cada intento en la bitácora `RegistroAcceso`.
   Reglas: QR válido, socio existente, membresía activa/vigente/pagada, *anti-passback* (no entra dos veces) y aforo disponible. Modo AUTO: si está dentro registra salida.
3. **Reserva de clases con cupos y lista de espera**
   Las clases tienen programación semanal y se generan sesiones para 7 días. Al reservar: si hay cupo → `CONFIRMADA`; si no → `EN_ESPERA`. Al cancelar una confirmada, el primero en espera (orden de llegada) pasa a confirmado automáticamente; también al aumentar el cupo. Se usa `SELECT … FOR UPDATE` para evitar sobrecupo con reservas simultáneas.
4. **Dashboard de aforo en tiempo real**
   `/dashboard/` consulta `GET /api/aforo/` cada 3 segundos (fetch asíncrono): ocupación vs capacidad, alerta por umbral, personas dentro, últimos accesos, denegados, latencia promedio y clases del día.

## 🏗️ Arquitectura

```
Navegador ──HTTP:80──► Nginx ──► Gunicorn (Django + DRF) ──► PostgreSQL 16
                          └─ /static/ (archivos estáticos)       (misma EC2)
```

```
config/              Configuración del proyecto (settings, urls, wsgi)
gym/
  models.py          Modelos: Plan, Socio, Membresia, Pago, Clase, Sesion, Reserva, RegistroAcceso, Instalacion
  services.py        Reglas de negocio (reservas, lista de espera, validación QR, aforo)
  api.py, serializers.py, api_urls.py   API REST con DRF
  views.py, forms.py, urls.py           Vistas HTML (templates) y CRUD
  admin.py           Django Admin personalizado (filtros, búsquedas, inlines, acciones, colores)
  permissions.py     Roles: Administrador, Recepción, Socio
  tests.py           20 pruebas automáticas
  management/commands/  seed_demo, generar_sesiones, actualizar_membresias
templates/           base, login, dashboard, acceso, clases, mi_cuenta, CRUD y admin/base_site.html
static/              CSS/JS propios (Bootstrap 5.3.3 vía CDN jsDelivr)
deploy/setup_ec2.sh  Script de instalación en EC2 (PostgreSQL + Gunicorn + Nginx + cron)
prompts_ia.md        Registro del apoyo de IA
```

### Seguridad
- Autenticación por sesión de Django (login/logout), sesión de 8 h que expira al cerrar el navegador, cookies `HttpOnly`.
- CSRF en formularios y en llamadas `fetch` (cabecera `X-CSRFToken`).
- Permisos por rol en vistas HTML (`LoginRequiredMixin` + `UserPassesTestMixin`) y en la API (clases `BasePermission` propias). Un socio solo ve sus propias membresías y reservas.
- Contraseñas con los validadores de Django; `SECRET_KEY` y credenciales de BD en `.env` (fuera del repositorio).
- `DEBUG=False` en producción, `X-Frame-Options: DENY`, `nosniff`.
- Bitácora de accesos de solo lectura en el Admin (auditoría).

## 🔌 Endpoints principales de la API

| Método | Ruta | Rol |
|---|---|---|
| GET/POST/PUT/PATCH/DELETE | `/api/planes/` | lectura: autenticado · escritura: recepción |
| CRUD | `/api/socios/`, `/api/pagos/` | recepción |
| CRUD | `/api/membresias/` (+ `/{id}/congelar/`, `/{id}/descongelar/`) | recepción (socio: solo lectura propia) |
| CRUD | `/api/clases/`, `/api/sesiones/` | lectura: autenticado |
| POST | `/api/sesiones/{id}/reservar/` | socio / recepción |
| GET/POST | `/api/reservas/` · POST `/api/reservas/{id}/cancelar/` | socio (propias) / recepción |
| POST | `/api/acceso/validar/` `{"codigo": "<uuid>", "tipo": "AUTO"}` | recepción |
| GET | `/api/aforo/` | recepción |
| GET | `/api/accesos/`, `/api/me/`, `/api/salud/` | recepción / autenticado / público |

## 💻 Ejecución local

Requisitos: Python 3.12+, PostgreSQL 14+.

```bash
git clone https://github.com/Joaquin896/fitlife-eva3-backend.git
cd fitlife-eva3-backend
python3 -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Crear base de datos
sudo -u postgres psql -c "CREATE USER fitlife WITH PASSWORD 'fitlife_local' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE fitlife OWNER fitlife;"

cp .env.example .env            # editar DB_PASSWORD=fitlife_local y DJANGO_DEBUG=True
python manage.py migrate
python manage.py seed_demo      # datos de demostración
python manage.py createsuperuser
python manage.py runserver      # http://127.0.0.1:8000
python manage.py test gym       # pruebas automáticas
```

## ☁️ Despliegue en AWS EC2 (AWS Academy Learner Lab)

1. **Security Group** `fitlife-sg`: entrada TCP 22 (SSH) y TCP 80 (HTTP) desde `0.0.0.0/0`.
2. **Instancia**: Ubuntu Server 24.04 LTS, `t3.small`, key pair `vockey`, 20 GB gp3, el security group anterior.
3. En **Advanced details → User data** pegar:

   ```bash
   #!/bin/bash
   export REPO_URL=https://github.com/Joaquin896/fitlife-eva3-backend.git
   export ADMIN_PASSWORD='CAMBIAR_ESTA_CLAVE'
   export DEMO_PASSWORD='FitLife2026!'
   git clone "$REPO_URL" /opt/fitlife
   bash /opt/fitlife/deploy/setup_ec2.sh
   ```
4. Esperar ~5 minutos y abrir `http://IP_PUBLICA/`. Log de instalación: `/var/log/fitlife-setup.log`.
5. (Recomendado) Asociar una **Elastic IP** para que la IP no cambie al reiniciar el laboratorio.

Actualizar el código en el servidor:
```bash
ssh -i labsuser.pem ubuntu@IP_PUBLICA
sudo REPO_URL=x ADMIN_PASSWORD=x bash /opt/fitlife/deploy/setup_ec2.sh   # hace git pull, migrate y reinicia
```

Comandos útiles en la EC2:
```bash
sudo systemctl status fitlife nginx postgresql
sudo journalctl -u fitlife -n 50
sudo -u postgres psql fitlife -c "\dt"
```
