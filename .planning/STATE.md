# Estado del Proyecto

## Sesión actual
- **Fecha:** 2026-05-24
- **Fase activa:** todas completadas
- **Estado:** `completed` ✅

## Progreso de fases
| Fase | Estado | Completado |
|------|--------|-----------|
| 1 — Fundación e Infraestructura | `completed` | 100% |
| 2 — Agente Dropi | `completed` | 100% |
| 3 — Research Agent | `completed` | 100% |
| 4 — Campaign Agent | `completed` | 100% |
| 5 — Analytics Agent | `completed` | 100% |
| 6 — Orquestador | `completed` | 100% |
| 7 — Dashboard | `completed` | 100% |

## Decisiones clave tomadas
- Stack: Python + FastAPI + LangGraph + Playwright
- LLM: Claude claude-sonnet-4-6 para generación de copy y decisiones del orquestador
- Dropi: acceso via scraping Playwright (sin API oficial)
- Presupuesto infra: < $200 USD/mes
- Autonomía: 100% — sin aprobación humana

## Credenciales pendientes de configurar
- [ ] Anthropic API Key
- [ ] Meta Business Manager + Graph API token
- [ ] TikTok Marketing API credentials
- [ ] Google Ads API + Developer Token + OAuth2
- [ ] Dropi username / password
- [ ] Telegram Bot token (alertas)

## Próximo paso
Proyecto MVP completo. Configurar credenciales en `.env` y hacer deploy.

## Notas
- Inicializado el 2026-05-24 via `/gsd:new-project`
- El proyecto está en carpeta: `c:\Users\PT\Desktop\Proyectos Juan\Dropi`
