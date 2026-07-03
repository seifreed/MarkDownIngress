# Clean Code / Clean Architecture Score (2026-07-04)

## Resultado global
**7.9 / 10**

## Diagnóstico por dimensión (evidencia)

- **Calidad estática base: 10/10**
  - `ruff check markdown_ingress tests` ✅
  - `black --check markdown_ingress tests` ✅
  - `mypy markdown_ingress` ✅
  - `bandit -r markdown_ingress` ✅

- **Arquitectura por capas: 8/10**
  - Estructura de carpetas limpia (`api_server`, `adapters`, `application`, `core`).
  - Se redujo acoplamiento de estados de cola API creando `api_server_job_queue_states.py`.
  - Algunas piezas de orquestación siguen concentradas en `api_server.py` (~723 líneas).

- **Clean Code local: 8/10**
  - Nombres y contratos consistentes; constantes centralizadas en módulos por dominio.
  - Quedan duplicados relevantes de estados y literales SQL en capa de jobs.

- **Prueba y seguridad: 7.5/10**
  - Gates de calidad pasan.
  - `make test-fast` y pruebas de API pasan.
  - Cobertura no homogénea: módulos críticos bien cubiertos vs. otros con bajo ejercicio (no indicador de ruptura, sí de deuda).

## ¿Cumple clean code / clean architecture 10?
**No.** No llegó a 10 aún; falta deuda acumulada en acoplamiento e histórico de módulos monolíticos y duplicación de estados en capa adapters.

## Cambios recientes (avance)
- `a651bd0`: Centraliza `api_server_job_queue_states`.
- `aee6322`: Usa esas constantes en todos los helpers API de cola relevantes (`external_owner`, `backend_error`, `open`).

## Bloques para seguir hasta 10
1. Extraer el resto de estado/constantes de cola en `adapters/jobs` y unificar estado/valores de dominio.
2. Reducir responsabilidades de `api_server.py` separando orquestación/estado de rutas.
3. Añadir pruebas de contrato de estado de cola en adapters/jobs para evitar regresiones de estado.
