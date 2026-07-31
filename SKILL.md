---
name: ib-maestro
description: IB Macro de Voyager/VMC Subastas. Carga este skill para obtener la arquitectura de intención completa del proyecto — Intention Matrix + Purpose Pyramid validados. Prerequisito antes de ejecutar cualquier fase o sub-IB.
type: reference
project: VMC Subastas — VOYAGER (UI Upgrade)
captured: 2026-04-13
revised: 2026-04-13
context: IB Macro construido co-creativamente. Establece la arquitectura de intención completa de Voyager — necesidad, apuesta estratégica, 5 challenges, hipótesis y pirámide de propósito validados. Prerequisito de todos los sub-IBs de fase.
---

# VOYAGER — Intention Builder Macro

**Type:** reference
**Project:** VMC Subastas — VOYAGER (UI Upgrade)
**Captured:** 2026-04-13
**Context:** IB Macro construido co-creativamente. Establece la arquitectura de intención completa de Voyager — necesidad, apuesta estratégica, 5 challenges, hipótesis y pirámide de propósito validados. Prerequisito de todos los sub-IBs de fase.

---

## KICKOFF

```
CODE NAME:    VOYAGER
WHY:          La interfaz de VMC genera desconfianza financiera en nuevos
              leads antes de que puedan evaluar el producto real. Voyager
              devuelve la autoridad visual que su trayectoria merece.
SCOPE:        Macro | UI upgrade total — UX flows y lógica de negocio
              del legacy se mantienen INTACTOS
STAKEHOLDERS: VMC Subastas (Perú) — plataforma transaccional de subastas
              de vehículos de alto valor
TEAM LEAD:    Arquitecto único + equipo de agentes AI orquestados
```

---

## PHASE 1 — INTENTION MATRIX

### Necessity

VMC Subastas opera una plataforma transaccional de alto valor cuya interfaz visual, visiblemente desactualizada frente a competidores directos, genera desconfianza financiera en nuevos leads antes de que puedan evaluar el producto real — impidiendo su conversión en postores activos.

El problema no es el producto — VMC es la plataforma pionera de subastas digitales de vehículos en Perú, con el mayor historial del mercado. El problema es que la UI actual actúa como filtro de entrada: un nuevo lead llega, compara con competidores que se ven modernos, y se va antes de descubrir el valor real. En una plataforma de alto valor donde la confianza financiera es la moneda de entrada, la pregunta que se hace el lead no es "¿me gusta este diseño?" — es "¿le voy a dar mi dinero a esto?"

### Supposition

Al modernizar su interfaz, VMC Subastas recupera su posición perceptual como pionera y referente del mercado de subastas digitales en Perú — generando en nuevos leads la confianza financiera necesaria para convertirse en postores activos desde el primer contacto.

La antigüedad de VMC no es un lastre — es un activo que la UI actual esconde. Voyager no es un rediseño cosmético: es remover el bloqueador de conversión que impide que la trayectoria real de VMC sea percibida. Cuando Voyager esté live, un nuevo lead entrará y sentirá que está en la plataforma que fundó este mercado en Perú, no en una que lo copió mal.

### Challenges & Solutions

#### CHALLENGE A — Capacidad de ejecución unipersonal

Una sola persona no puede ejecutar un design system completo — la capacidad humana es el techo. Un design system enterprise requiere normalmente un equipo multidisciplinario trabajando en paralelo durante meses. Sin esa estructura, la iniciativa no puede completarse en el tiempo requerido con un modelo de ejecución tradicional.

**Solution:** Reemplazar el modelo de equipo tradicional por una arquitectura de agentes AI orquestados que multiplican la capacidad de ejecución. El Arquitecto humano gobierna — define intención, aprueba planes, valida outputs en los puntos de inflexión. Los agentes ejecutan dentro del dominio autorizado. La capacidad no está limitada por horas humanas disponibles.

#### CHALLENGE B — Brecha de expertise en dominios críticos

Los dominios críticos del proyecto (frontend, UI, product design, SEO, accesibilidad) requieren expertise especializado que no existe internamente y no puede contratarse dentro de la ventana de ejecución. Sin ese expertise, los outputs pueden ser técnicamente ejecutados pero estratégicamente incorrectos — componentes que no pasan validación de accesibilidad, estructuras que no son SEO-friendly, decisiones de UI que no siguen los principios del design system.

**Solution:** Agentes expertos por dominio — cada uno actúa con criterio propio sin depender del conocimiento del Arquitecto. Un agente de frontend conoce los estándares de código. Un agente de UI conoce el design system. Un agente de accesibilidad conoce WCAG y APCA. El Arquitecto no necesita ser experto en cada dominio — necesita gobernar el sistema que los integra.

#### CHALLENGE C — Ventana de 4 meses no negociable

La ventana de ejecución es de 4 meses máximo. Los competidores ya tienen ventaja visual consolidada — cada mes que pasa es un mes más de leads que llegan a VMC, comparan con la competencia, y se van. El tiempo no es solo una restricción operacional: es una variable competitiva activa.

**Solution:** Fases secuenciales con entregables bloqueados — ninguna fase avanza sin validación de la anterior. La disciplina de fase no es burocracia: es lo que garantiza que los 4 meses produzcan un sistema funcional y no un trabajo a medias en múltiples frentes. Los sub-IBs de cada fase definen exactamente qué debe existir antes de avanzar.

#### CHALLENGE D — Riesgo de deuda invisible entre fases

Sin una capa de validación entre fases, outputs incorrectos avanzan silenciosamente. A diferencia de un bug de código que falla ruidosamente, un componente con tokens hardcodeados, una tipografía incorrecta, o un estado de hover mal derivado produce deuda invisible — se acumula sin ser detectada hasta que el sistema entero está contaminado. En un design system, la deuda invisible es especialmente peligrosa porque se multiplica: un token mal aplicado en un componente base contamina todos los componentes que lo usan.

**Solution:** Capa de gatekeepers por transición de fase con criterios de salida explícitos y no negociables. Cada fase tiene un contrato de salida: qué debe existir, qué debe pasar validación, qué no puede avanzar. El gatekeeper no es una persona — es un agente que audita el output contra los criterios antes de dar paso a la siguiente fase. Nada avanza sin aprobación.

#### CHALLENGE E — Riesgo de scope creep sobre el legacy

Voyager es UI-only. Los flows de UX y la lógica de negocio del legacy se mantienen intactos. Pero bajo presión de tiempo, con agentes ejecutando en paralelo, el riesgo de que un cambio de componente arrastre lógica de negocio es real. No siempre es intencional — puede ser un agente que "mejora" un comportamiento que en realidad es un flow de negocio, o una refactorización que rompe algo que el legacy necesita.

El agravante: VMC no tiene un repositorio de componentes documentado. No hay inventario de lo que existe. Eso significa que los límites del scope no están explícitamente marcados — tienen que ser enforced por gobernanza, no por estructura.

**Solution:** Límite UI-only enforced por gobernanza: ningún agente modifica flows ni lógica de negocio sin aprobación explícita del Arquitecto. El gatekeeper de scope valida en cada transición que los cambios se limiten a la capa visual. La taxonomía del legacy (ib-taxonomia) es el primer entregable precisamente para mapear esos límites antes de que cualquier agente empiece a tocar código.

### Hypothesis

Voyager demuestra que VMC Subastas puede recuperar y superar a sus competidores sin igualar sus recursos — un Arquitecto que orquesta agentes construye en 4 meses lo que equipos tradicionales tardan años, y el design system AI-readable resultante convierte esa velocidad de ejecución en una ventaja estructural que ningún competidor con modelo convencional puede replicar fácilmente: más rápido al mercado, más consistente en cada entrega, con la confianza financiera que VMC siempre mereció proyectar.

El mecanismo compuesto: el design system AI-readable no es un entregable estático — es infraestructura que se vuelve más valiosa con el tiempo. Cada nuevo componente, landing o feature que se construya sobre él es más rápido que el anterior. El Arquitecto que conoce el sistema puede extenderlo con un prompt. La ventaja no es solo Voyager — es todo lo que viene después de Voyager.

---

## PHASE 2 — PURPOSE PYRAMID

### Task Base

```
TASK A — Establecer la arquitectura de intención y gobernanza
          IBs completos, reglas de agentes, criterios de cada fase —
          la base que todo lo demás requiere para existir

TASK B — Construir la infraestructura AI de ejecución
          Agentes especializados por dominio, orquestador central,
          capa de gatekeepers y herramientas conectadas (Jira u otras)

TASK C — Desarrollar el design system completo
          Las 4 fases internas: taxonomía → fundamentos → componentes → handoff
          ▲ KEYSTONE

TASK D — Validar integridad en cada transición de fase
          Gatekeeper activo en cada handoff — criterios de salida
          explícitos, cero outputs no certificados avanzan

TASK E — Activar en producción y medir impacto
          Deploy, métricas de conversión de leads, velocidad de
          ejecución futura validada contra la hipótesis
```

### Objectives

```
── SECTION A ──────────────────────────────────────────────────────────────

OBJECTIVE 1 [Tasks A → B]
Voyager tiene gobernanza operativa activa — criterios definidos, agentes
configurados y orquestador coordinando el flujo de trabajo antes de que
se escriba una sola línea de código del design system.

OBJECTIVE 2 [Tasks B → C] ▲ KEYSTONE BRIDGE
El design system de Voyager existe como sistema funcional — tokens,
componentes y variantes disponibles para producción, construidos por
agentes especializados sobre la infraestructura establecida.

── SECTION B ──────────────────────────────────────────────────────────────

OBJECTIVE 3 [Tasks C → D]
Cada entregable de Voyager ha pasado validación de gatekeeper — cero
outputs no certificados avanzan a la siguiente fase. La integridad del
sistema es continua, no puntual.

OBJECTIVE 4 [Tasks D → E]
Voyager está live con métricas que confirman impacto en conversión de
leads y velocidad de ejecución futura — la hipótesis se valida contra
resultados reales, no supuestos.
```

### Responsibilities

```
RESPONSABILIDAD X [Section A | Central Task: B]
Garantizar que la infraestructura de agentes permanezca operativa,
coordinada y alineada a los criterios de gobernanza durante toda
la iniciativa.

RESPONSABILIDAD Y [Section B | Central Task: D]
Garantizar que ningún output avance entre fases sin cumplir los
criterios de calidad — protegiendo la integridad del sistema
en todo momento.
```

### Purpose

```
Devolver a VMC Subastas la autoridad visual que su trayectoria merece
— construido por agentes gobernados por intención, AI-driven para
escalar sin equipos tradicionales y AI-readable para que cada nuevo
componente o experiencia nazca bajo un ecosistema AI.
```

---

## VALIDACIÓN — STATUS: VALIDATED ✓

```
CHECK 1 — Ruta funcional ejecutable:      ✓
CHECK 2 — Responsabilidades resuelven:    ✓
CHECK 3 — Propósito refuerza hipótesis:   ✓
CHECK 4 — Estructura sin vacíos:          ✓
CHECK 5 — Entendible sin contexto:        ✓
CHECK 6 — Eficiente, sin redundancias:    ✓
```

---

## Key Architectural Decisions

- Voyager es UI-only — UX flows, UX writing y lógica de negocio del legacy se mantienen INTACTOS sin excepción
- El Arquitecto humano gobierna; los agentes ejecutan — la autonomía de agentes opera dentro del dominio autorizado por fase
- Los IBs se construyen una vez y son el cimiento — no son herramientas de runtime, son la arquitectura de intención que los agentes operan dentro de
- Los gatekeepers no son opcionales — son la infraestructura de confianza del sistema. Sin ellos, la deuda invisible se acumula silenciosamente
- El design system es AI-readable por diseño — tokens en W3C DTCG, componentes con criterios explícitos, reglas encapsuladas en skills
- No hay repositorio de componentes legacy — la taxonomía (ib-taxonomia) es el primer entregable antes de tocar cualquier código
- Stack: Next.js App Router · TypeScript estricto · Tailwind CSS v4 · RTK Query · Zustand · Terrazzo · pnpm
- Pipeline de tokens: tokens.json (OKLCH) → Terrazzo CLI → tokens.css → componentes via var(--token). Nunca HEX en código de producción
- 4 meses es la ventana máxima — la disciplina de fase secuencial con entregables bloqueados es lo que hace ese plazo alcanzable

---

## Revision Log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-04-13 | IB Macro construido co-creativamente en sesión. Todos los elementos validados. |

---

## Source

Sesión: VMC Subastas — Voyager IB Macro build, 2026-04-13
