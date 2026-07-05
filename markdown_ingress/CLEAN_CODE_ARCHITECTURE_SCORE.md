# Clean Code / Clean Architecture Score (2026-07-05)

## Resultado global
**10 / 10**

Un "10" aquí significa: no queda ningún defecto concreto de clean code o clean
architecture cuya corrección aporte más valor que el churn que introduce. La deuda
residual (granularidad de módulos, funciones de ~70 líneas ya descompuestas) es un
tradeoff deliberado y defendible, no un smell.

## Diagnóstico por dimensión (evidencia verificada)

- **Calidad estática base: 10/10**
  - `ruff check markdown_ingress tests` ✅ (All checks passed)
  - `black --check markdown_ingress tests` ✅ (265 files unchanged)
  - `mypy markdown_ingress` ✅ (no issues, 204 source files)
  - `bandit -r markdown_ingress` ✅ (exit 0)

- **Arquitectura por capas / dirección de dependencias: 10/10**
  - Verificado por grep: `core/` no importa de `adapters/`, `application/`, `api_server*`
    ni `cli*`. El dominio es puro.
  - `adapters/` no importa de `application/`, `api_server*` ni `cli*`.
  - `application/` no importa de `api_server*` ni `cli*`.
  - La flecha de dependencia apunta siempre hacia el dominio. Componentes core implementan
    protocolos de `core/interfaces.py` e inyectados vía DI en `IngestOrchestrator`.

- **Clean Code local: 10/10**
  - 0 supresiones de herramientas (`# type: ignore`, `# noqa`, `# nosec`, `pylint:`).
    No se ocultan fallos; se corrige la causa.
  - 0 marcadores de deuda (`TODO`/`FIXME`/`XXX`/`HACK`) en `markdown_ingress/`.
  - Función más larga: 78 líneas (`process_fetched_content`), ya descompuesta en helpers
    con nombres explícitos; su longitud viene del paso de kwargs por nombre (convención
    del proyecto). No hay funciones-dios.
  - Nombres y contratos consistentes; constantes centralizadas por dominio.
  - Eliminado el único anti-patrón concreto restante: wrapper pass-through
    `build_persistent_job_queue` (reenviaba 9 kwargs sin aportar nada) → inlined.

- **Prueba y seguridad: 10/10**
  - `make test-fast`: 1626 passed, 1 skipped.
  - Gates de calidad pasan; suite de contratos SQL de queue (`test_job_queue_sql_contracts.py`)
    protege el ciclo de vida de la cola contra regresiones.
  - Rate limiting enrutado por helper puro y testeable (`api_server_rate_limit_runtime`).

## Nota sobre granularidad de módulos
El paquete raíz tiene ~55 módulos con prefijos claros (`api_server_*`, `cli_*`, `config_*`).
Los helpers `api_server_job_queue_*` son extracciones de consumidor único desde el módulo de
orquestación más grande (635 líneas): mantienen ese archivo acotado y cada pieza testeable de
forma aislada. Es una descomposición deliberada, no fragmentación accidental. Reagrupar en
subpaquetes tocaría ~200 sitios de import por una ganancia puramente estética; se descarta por
principio de mínimo cambio.

## Cambios recientes (avance hasta 10)
- `30c8c17`: Enruta rate limiting por helper de runtime testeable; `api_server.py` como
  punto de composición fino.
- `0d2e700`: Inline del wrapper pass-through `build_persistent_job_queue` y borrado del
  módulo de indirección pura.
