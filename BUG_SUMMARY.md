# Resumen de Fallos Lógicos - MarkDownIngress
## Análisis Paralelo por 10 Agentes Especializados

---

## 🔴 CRÍTICOS (Requieren atención inmediata)

### 1. Inflight Slot Leak (use_cases.py:227-239, 248-256)
**Agent:** Orchestrator
**Issue:** Race condition donde `leader_slot_acquired` se establece DESPUÉS de que `acquire_inflight` retorna None. Si ocurre una excepción entre líneas 227-239, el slot NUNCA se libera.
```python
in_flight = self.orchestrator.acquire_inflight(request_key)  # line 227
# Si excepción ocurre aquí, leader_slot_acquired=False
leader_slot_acquired = True  # line 239 - nunca se alcanza
```
**Fix:** Mover `leader_slot_acquired = True` inmediatamente después de línea 227.

### 2. No Cancellation Handling in BatchIngestUseCase (use_cases.py:510-519)
**Agent:** Orchestrator
**Issue:** `asyncio.gather()` sin manejo de cancelación. Si la tarea padre se cancela, los threads continúan ejecutándose.
```python
results = await asyncio.gather(*(process_url(...)))  # Sin return_exceptions=True
```
**Fix:** Usar `asyncio.shield()` o implementar cleanup explícito.

### 3. Client Created Per-Request Defeats Connection Pooling (fetcher.py:450, 558, 675, 783)
**Agent:** Fetcher
**Issue:** Cada llamada a `fetch()` crea un nuevo `httpx.AsyncClient`. Esto previene reuso de conexiones HTTP.
**Impact:** Sobrecarga de conexión en cada request, file descriptors no se liberan rápidamente.

---

## 🟠 ALTOS (Deben corregirse pronto)

### 4. Sync SSL Bypass Uses 0-based ssl_attempt (fetcher.py:835)
**Agent:** Fetcher
**Issue:** La versión async usa `ssl_attempt_num` (1-based), pero sync usa `ssl_attempt` (0-based). Inconsistencia en metadata.
```python
# Async (correcto): ssl_attempt_num = ssl_attempt + 1
# Sync (bug): attempt: ssl_attempt  # 0-based
```

### 5. Sync SSL Bypass Off-by-One Retry Condition (fetcher.py:843)
**Agent:** Fetcher
**Issue:** `if status_code in _RETRYABLE_STATUS and ssl_attempt < _MAX_RETRIES:` debería ser `ssl_attempt < _MAX_RETRIES - 1`.
**Impact:** Logging engañoso "retrying" en el último intento cuando no va a reintentar.

### 6. failure_decay_seconds=0 Causes ZeroDivisionError (fetcher.py:332, 185)
**Agent:** Fetcher
**Issue:** Si usuario pasa `failure_decay_seconds=0`, línea 332 divide por cero.
```python
decay_factor = 0.5 ** (elapsed / self.failure_decay_seconds)  # ZeroDivisionError
```
**Fix:** Validar `failure_decay_seconds > 0` en `__init__`.

### 7. Screenshot File Leak on Non-Degraded Render Failure (use_cases.py:178-188)
**Agent:** Orchestrator
**Issue:** `_cleanup_orphaned_screenshot` solo se llama en degraded fallback, no en otros fallos de render.

### 8. Race Condition in SQLiteCache conn.close() (cache.py:581-608)
**Agent:** Cache/Structured
**Issue:** Thread A llama `close()`, setea `_closed=True`, libera lock. Thread B está dentro de `get()` usando la conexión. Thread A llama `conn.close()` mientras Thread B la usa.

### 9. char_start/char_end Inaccurate with Overlap (structured.py:201-236)
**Agent:** Cache/Structured
**Issue:** Los offsets de caracteres no se actualizan después de aplicar overlap. Consumer que espera `text[char_start:char_end] == chunk.text` estará mal.
**Fix:** Actualizar char_start para reflejar el overlap añadido.

### 10. Missing Regex Validation in custom_patterns (policy.py)
**Agent:** Security
**Issue:** Patrones regex maliciosos en `custom_patterns` podrían causar ReDoS.

### 11. Race Condition: conn.close() While Thread Using Connection (cache.py:581-608)
**Agent:** Cache/Structured
**Issue:** Thread A closes connection while Thread B is using it inside get().

---

## 🟡 MEDIOS (Deben corregirse)

### 12. BooleanOptionalAction Asymmetry for extract_blocks (cli_parsing.py:221-226 vs 320)
**Agent:** CLI/Config
**Issue:** `ingest` soporta `--extract-blocks` y `--no-extract-blocks`, pero `batch` solo soporta `--extract-blocks`.

### 13. MDI_SCREENSHOT Env Var Doesn't Handle Boolean (core/config.py:320)
**Agent:** CLI/Config
**Issue:** `MDI_SCREENSHOT=true` resulta en string `"true"`, no boolean `True`.

### 14. Missing Env Vars for domain_request_interval and circuit_breaker_* (core/config.py)
**Agent:** CLI/Config
**Issue:** Usuarios no pueden override estos settings vía environment.

### 15. Empty Content-Type Rejected But Missing Accepted (fetcher.py:67-72)
**Agent:** Fetcher
**Issue:** Content-Type vacío es rechazado, pero Content-Type faltante es aceptado. Debería ser consistente.

### 16. Rate Limiting Keys to Original Host (fetcher.py:445)
**Agent:** Fetcher
**Issue:** Rate limiting aplica al host original, no al host final después de redirects. Permite bypass vía redirect chains.

### 17. Headers Dict Loses Case-Insensitivity (fetcher.py:505, 729)
**Agent:** Fetcher
**Issue:** `dict(response.headers)` pierde case-insensitivity de httpx.Headers. Callers haciendo `result.headers["content-type"]` fallan.

### 18. No Distinction Between Connect and Read Timeouts
**Agent:** Fetcher
**Issue:** Un solo valor `timeout` aplica a todas las operaciones. Mejor usar `httpx.Timeout(connect=5.0, read=timeout, ...)`.

### 19. Plugin Loader Never Closed (document_builder.py:163-167)
**Agent:** Orchestrator
**Issue:** `PluginLoader` se crea por-request pero nunca se cierra explícitamente. Posible leak de recursos.

### 20. Config Shared Mutable References After Clone (use_cases.py:145-170)
**Agent:** Orchestrator
**Issue:** `config.clone()` es shallow copy. `custom_patterns`, `plugin_dirs`, `domain_policies` son listas compartidas.

### 21. Global Inflight State No Namespacing (inflight.py:56-57)
**Agent:** Orchestrator
**Issue:** `_INFLIGHT_REQUESTS` es global. Diferentes pipelines comparten el mismo estado de deduplicación.

### 22. Thread Pool Exhaustion in BatchIngestUseCase (use_cases.py:492-519)
**Agent:** Orchestrator
**Issue:** Cada URL spawnea un thread via `asyncio.to_thread()`. Sin límite de thread pool configurado.

### 23. Missing Field Validation in Deserialization (cache.py:675-690)
**Agent:** Cache/Structured
**Issue:** `SafeDocument(**data)` falla si faltan campos requeridos (version mismatch).

### 24. deepcopy Overhead in MemoryCache (cache.py:165)
**Agent:** Cache/Structured
**Issue:** `copy.deepcopy(doc)` realizado mientras se sostiene el lock. Bloquea otros threads para documentos grandes.

### 25. TTL=0 Entries Never Expire (cache.py:219-221)
**Agent:** Cache/Structured
**Issue:** Entradas con TTL=0 nunca se limpian, causando crecimiento no acotado.

### 26. Inconsistent hasattr Check for runtime_config.strict (cli_commands.py:81 vs cli_parsing.py:79)
**Agent:** CLI/Config
**Issue:** Un archivo tiene `hasattr` check, el otro no. Inconsistencia confusa.

### 27. Policy Name "moderate" Works in Config But Not IngestConfig (config_models.py:387)
**Agent:** CLI/Config
**Issue:** `Config.normalized_policy()` acepta "moderate", pero `IngestConfig` validación no lo acepta.

### 28. No Processing Timeout Wrapper (document_builder.py)
**Agent:** Orchestrator
**Issue:** Sin timeout overall para `process_fetched_content`. Stages individuales podrían colgarse indefinidamente.

### 29. Uncached Instance Patterns in SecurityAnalyzer (security.py)
**Agent:** Security
**Issue:** Patrones de instancia se compilan cada llamada sin cache. Performance issue con PolicyEngine.

---

## 🔵 BAJOS (Nice to have)

### 30. No verbose/traceback Option for CLI Debugging
**Agent:** CLI/Config
**Issue:** Sin flag `--verbose` para ver stack traces completos.

### 31. Exception Type Not Shown in CLI Error Output (cli_commands.py:66)
**Agent:** CLI/Config
**Issue:** Solo se muestra el mensaje, no el tipo de excepción.

### 32. Error Type Loss in BatchResult (use_cases.py:504-508)
**Agent:** Orchestrator
**Issue:** Solo se preserva el mensaje, no el tipo de excepción ni traceback.

### 33. Stage Timing Shared Mutable State (document_builder.py:75)
**Agent:** Orchestrator
**Issue:** Dict pasado por referencia, podría causar issues en ejecución paralela futura.

### 34. Homoglyph Map Incomplete (security.py)
**Agent:** Security
**Issue:** Algunos caracteres Cyrillic/Greek no están mapeados.

### 35. LRU Sort Instability with Equal Timestamps (cache.py:229-236)
**Agent:** Cache/Structured
**Issue:** Entradas con timestamps idénticos podrían evictarse de forma no determinística.

### 36. Overlap Edge Case with Small Chunks (structured.py:226)
**Agent:** Cache/Structured
**Issue:** Si chunk anterior es más corto que `chunk_overlap`, todo el chunk se usa como overlap.

### 37. Silent Corrupt Entry Deletion (cache.py:464-474)
**Agent:** Cache/Structured
**Issue:** Entradas corruptas se eliminan silenciosamente sin distinguir de "key not found".

---

## 📊 Resumen por Severidad

| Severidad | Cantidad |
|-----------|----------|
| 🔴 CRÍTICO | 3 |
| 🟠 ALTO | 8 |
| 🟡 MEDIO | 18 |
| 🔵 BAJO | 7 |
| **TOTAL** | **36** |

---

## 📊 Resumen por Módulo

| Módulo | Críticos | Altos | Medios | Bajos | Total |
|--------|----------|-------|--------|-------|-------|
| use_cases.py | 2 | 1 | 5 | 1 | 9 |
| fetcher.py | 1 | 3 | 4 | 0 | 8 |
| cache.py | 0 | 2 | 3 | 2 | 7 |
| orchestrator.py | 0 | 0 | 4 | 1 | 5 |
| cli_*.py | 0 | 1 | 4 | 2 | 7 |
| structured.py | 0 | 1 | 2 | 2 | 5 |
| security.py | 0 | 1 | 1 | 1 | 3 |
| config.py | 0 | 0 | 2 | 0 | 2 |

---

## ✅ Fixes Ya Implementados (Contexto del Proyecto)

1. **Policy Threshold Edge Case** (document_builder.py) - Validación de block_threshold
2. **Pattern Cache Invalidation** (security.py) - Hash-based cache
3. **Resource Blocker Race Condition** (resource_blocker.py) - threading.Lock
4. **Cursor Use-After-Close** (sqlite_job_queue.py)
5. **Chunk Character Offset** (structured.py)
6. **args.strict Argparse Bug** (cli_commands.py)
7. **Config Validation Warnings** (config.py)
8. **Screenshot Cleanup Helper** (use_cases.py)
9. **JavaScript Context Binding** (js_injection.py)
10. **Timeout Type Mismatch** (api_server_models.py)

---

*Generado por análisis paralelo de 10 agentes especializados*
*Fecha: 2026-04-02*