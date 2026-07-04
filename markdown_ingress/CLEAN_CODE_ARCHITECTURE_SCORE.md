# Clean Code / Clean Architecture Score (2026-07-04)

## Resultado global
**8.1 / 10**

## Diagnóstico por dimensión (evidencia)

- **Calidad estática base: 10/10**
  - `ruff check markdown_ingress tests` ✅
  - `black --check markdown_ingress tests` ✅
  - `mypy markdown_ingress` ✅
  - `bandit -r markdown_ingress` ✅

- **Arquitectura por capas: 8.5/10**
  - Estructura de carpetas limpia (`api_server`, `adapters`, `application`, `core`).
  - Se redujo acoplamiento de estados de cola API creando `api_server_job_queue_states.py`.
  - Se centralizaron estados de job en `core/job_status.py` y se eliminaron literales duplicados en API.
  - Algunas piezas de orquestación siguen concentradas en `api_server.py`.

- **Clean Code local: 8.2/10**
  - Nombres y contratos consistentes; constantes centralizadas en módulos por dominio.
  - Persisten patrones SQL repetidos en `adapters/jobs`, aunque sin divergencias de estado.

- **Prueba y seguridad: 7.5/10**
  - Gates de calidad pasan.
  - `make test-fast` y pruebas de API pasan.
  - Cobertura no homogénea: módulos críticos bien cubiertos vs. otros con bajo ejercicio (no indicador de ruptura, sí de deuda).

## ¿Cumple clean code / clean architecture 10?
**No.** Falta deuda residual en orquestación de `api_server.py` y consolidación de SQL de ciclo de vida de queue.

## Cambios recientes (avance)
- `a651bd0`: Centraliza `api_server_job_queue_states`.
- `aee6322`: Usa esas constantes en todos los helpers API de cola relevantes (`external_owner`, `backend_error`, `open`).
- `3f87a74`: Extrae hooks de runtime de cola en `api_server_queue_runtime_hooks.py`.
- `UNCOMMITTED`: Centraliza estados de job en `core/job_status.py`, reutilizados desde
  `adapters/jobs/job_queue_states.py` y `api_server_response_models.py`.

## Bloques para seguir hasta 10
1. Reducir responsabilidades de `api_server.py` separando orquestación/estado de rutas.
2. Consolidar contratos SQL del ciclo de vida de queue (lease / cleanup) para minimizar duplicación.
3. Añadir pruebas de contrato adicionales para estado de cola en `adapters/jobs`.
