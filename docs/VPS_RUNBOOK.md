# PromData VPS Runbook (Hetzner)

Este runbook prepara el despliegue futuro en VPS. **No se ha desplegado ni
cambiado infraestructura de producción.** Cloud Run se conserva como fallback.

## Topología y rutas públicas

`APP_DOMAIN` es el hostname canónico exacto, sin esquema, path ni `www` (por
ejemplo `app.example.com`). Se define una sola vez en el `.env` de la raíz.

| URL | Destino |
|---|---|
| `https://${APP_DOMAIN}/` | Next.js (`frontend:3000`) |
| `https://${APP_DOMAIN}/api/*` | FastAPI (`api:8000`) |
| `https://${APP_DOMAIN}/health/*` | FastAPI (`api:8000`) |

Las bases públicas (`NEXT_PUBLIC_API_BASE_URL`, `BACKEND_PUBLIC_URL` y
`FRONTEND_APP_URL`) son el origen `https://${APP_DOMAIN}` **sin `/api`**. El
frontend y los callbacks agregan `/api/v1/...` en el punto de uso.

## 1. Provisionar el host

- Recomendado para el primer ciclo: Hetzner CCX33 (8 vCPU / 32 GB RAM), Ubuntu
  LTS y disco suficiente para imágenes, AOF de Redis y backups.
- Configurar firewall de Hetzner: SSH (22) solo desde IPs administrativas, HTTP
  (80) y HTTPS (443). No abrir Redis (6379), FastAPI (8000) ni Next.js (3000) a
  Internet.
- Crear un usuario operativo con acceso SSH por llave y permisos controlados
  sobre Docker.
- Crear DNS `A` de `APP_DOMAIN` apuntando al VPS. Añadir `AAAA` solo si IPv6
  está configurado y probado. Caddy necesita DNS correcto y puertos 80/443
  accesibles para emitir TLS.

## 2. Preparar una revisión reproducible

1. Integrar los cambios mediante el flujo habitual y ejecutar la suite backend.
2. En el VPS, clonar el repositorio o actualizarlo explícitamente a un commit
   revisado (`git fetch` y `git checkout <commit>`). `deploy_hetzner.sh` no hace
   `git pull` y rechaza cambios rastreados locales.
3. Confirmar que los archivos versionados de Compose, Docker y Caddy pertenecen
   a esa misma revisión.

## 3. Configurar variables y secretos

Crear los archivos locales, ignorados por Git:

```bash
cp .env.production.example .env
cp backend/.env.production.example backend/.env
chmod 600 .env backend/.env
```

Editar ambos archivos en el VPS, sin pegar secretos en tickets, logs o commits:

- Raíz `.env`: fijar `APP_DOMAIN` al hostname elegido, credenciales públicas de
  Supabase (`NEXT_PUBLIC_SUPABASE_URL` y `NEXT_PUBLIC_SUPABASE_ANON_KEY`) y los
  valores de build que aparecen en la plantilla.
- `backend/.env`: configurar credenciales privadas de Supabase, `DEEPSEEK_API_KEY`
  y `GEMINI_API_KEY`. DeepSeek sirve análisis/narrativa; Gemini se conserva para
  embeddings de RAG/memoria. No sustituir las claves de ejemplo por valores
  ficticios.
- En el despliegue Compose, las URLs públicas se derivan de `APP_DOMAIN` y
  prevalecen sobre sus ejemplos en `backend/.env`. No añadir `/api` a las bases.
- Configurar en Google/Microsoft OAuth los callbacks HTTPS que correspondan;
  confirmar los route slugs vigentes en `backend/app/services/cloud_oauth.py`.
- Proteger también la copia externa de `.env` con almacenamiento cifrado y
  acceso limitado. No incluir `.env`, `backend/.env` ni sus copias en backups
  sin cifrado.

Antes del primer build, revisar que `APP_DOMAIN` sea solo hostname, y que DNS,
Supabase y las claves correspondan al entorno de producción.

## 4. Desplegar y verificar

Desde la raíz del repo en el VPS:

```bash
bash deploy_hetzner.sh
```

El script valida los archivos de entorno y la configuración Compose, construye
imágenes etiquetadas con el commit, levanta servicios y verifica dentro del
contenedor `/health/ready`, `/health/runtime` y `/health/observability`. Después
comprueba `https://${APP_DOMAIN}/health/ready` a través de Caddy. Si falla,
termina con error y muestra logs del servicio afectado; no hace rollback
destructivo automáticamente.

Verificación funcional adicional, ejecutada solo con autorización para crear y
limpiar un usuario temporal en el proyecto Supabase configurado:

```bash
cd backend
python scripts/smoke_post_deploy.py "https://${APP_DOMAIN}"
```

Confirmar también inicio de sesión, OAuth necesario, carga/análisis de un
archivo de prueba y cross-filter sobre un gráfico. En `/health/runtime`, revisar
que el proveedor activo sea `deepseek`; en observabilidad, comprobar Sentry y
los servicios requeridos del entorno.

## 5. Backups y restauración

- Supabase es el SoT de usuarios/datasets: habilitar y validar su política de
  backups/PITR apropiada al plan antes del go-live.
- Redis usa persistencia AOF y snapshot RDB. El backup manual crea un RDB
  consistente en un punto temporal y valida su integridad:

  ```bash
  bash scripts/vps_backup.sh
  ```

  Por defecto se guarda bajo `backups/redis/`, con permisos restrictivos; la
  carpeta está ignorada por Git. Cifrar y replicar cada backup fuera del VPS,
  definir retención y probar restauración. Los backups Redis pueden contener
  mensajes de tareas y resultados: tratarlos como datos sensibles.
- Guardar por separado, cifrada, una copia recuperable de los `.env`; el script
  de Redis no copia secretos.
- Restauración Redis (ventana de mantenimiento; se pierden escrituras posteriores
  al instante del snapshot):

  ```bash
  RDB_FILE=backups/redis/<snapshot>.rdb
  docker compose -f docker-compose.prod.yml stop api worker redis
  docker compose -f docker-compose.prod.yml cp "$RDB_FILE" redis:/data/dump.rdb
  docker compose -f docker-compose.prod.yml run --rm --no-deps -T \
    --entrypoint /bin/sh redis -c 'rm -rf /data/appendonlydir /data/appendonly.aof'
  docker compose -f docker-compose.prod.yml up -d redis
  docker compose -f docker-compose.prod.yml exec -T redis redis-cli ping
  docker compose -f docker-compose.prod.yml up -d api worker
  ```

  Validar healthchecks y procesar una tarea de prueba. Probar el procedimiento
  en un entorno aislado antes de depender de él.

## 6. Rollback de aplicación

Cada build queda etiquetado `promdata-frontend:<commit>` y
`promdata-backend:<commit>`. Registrar el SHA actualmente estable antes de cada
despliegue y no ejecutar `docker image prune` sin política explícita. Si el
release nuevo falla y sus imágenes anteriores siguen locales:

```bash
APP_BUILD_SHA=<sha-estable> docker compose -f docker-compose.prod.yml up -d --no-build
```

Revisar luego las tres rutas `/health/*` y el flujo funcional. Este rollback
revierte imágenes, no una migración de base de datos; las fases de VPS aquí
documentadas no ejecutan migraciones Supabase. Si también cambió configuración,
volver a la revisión Git que la define y validar el diff antes de re-desplegar.

## 7. Carga y apertura de tráfico

No asumir capacidad de cientos de usuarios por el tamaño del VPS. Antes de
abrir tráfico, realizar una prueba gradual de concurrencia (incluyendo 100–300
usuarios virtuales como objetivo de evaluación) y medir CPU/RAM, p95/p99,
errores 5xx, backlog de Celery, conexiones/memoria Redis, límites de Supabase y
cuota/rate-limit de DeepSeek. Ajustar concurrencia y límites solo a partir de
esas métricas. Verificar alertas y restauración de backup.

## Cloud Run

Cloud Run permanece como fallback sin cambios en esta fase. No cambiar DNS ni
eliminar servicios Cloud Run como parte del despliegue VPS; una vuelta al
fallback requiere revisar explícitamente sus URLs, CORS, secretos y versión de
imagen.
