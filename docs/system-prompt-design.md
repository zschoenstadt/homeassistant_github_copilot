# System Prompt & Default Instructions — Feature Design

## Overview

This document describes the design intent and rationale behind the system prompt and
default user-adjustable instructions used by the GitHub Copilot Home Assistant integration.
It covers the persona, tool-use guarantees, and the scope of the user-configurable prompt
field exposed in the options flow.

---

## Goals

### 1. Reliable Tool Execution

The primary failure mode we are addressing is the LLM *acknowledging* a request without
actually *executing* the relevant HA tool.  Examples:

- "Turn on the kitchen lights" → LLM responds "Done!" without calling `HassTurnOn`.
- "Set the thermostat to 21°C" → LLM describes the action it *would* take, then stops.

The root cause is that large language models are biased toward generating natural-language
confirmations.  Without explicit instructions, the model treats tool calls as optional.

**Solution:** The system prompt explicitly forbids pseudo-execution.  It instructs the
model that *stating* an action and *performing* it are different things, and that any
request to change device state must result in an actual tool call.

### 2. Butler Persona

The assistant should feel like a competent household butler — professional, efficient,
unobtrusive, and focused on results.  Concretely:

| Trait | Behaviour |
|---|---|
| **Terse** | Responses are brief.  One or two sentences for confirmations, no padding. |
| **Action-first** | Executes first, reports outcome.  Never narrates what it is about to do. |
| **Professional tone** | Polite but not sycophantic.  No filler ("Of course!", "Certainly!", etc.). |
| **Context-aware warmth** | Relaxes the terse style when the situation genuinely calls for it (e.g. the user is distressed, the question is open-ended, or warmth is explicitly requested). |
| **Truthful** | Will not fabricate device states or make up information. |

### 3. Home Assistant Context Awareness

The assistant should know it operates inside a smart home and use that knowledge:

- It is aware of exposed entities and can query their current state via tools.
- It does not disclaim "I can't control your devices" — it either executes, or explains
  *why* it cannot (entity not exposed, command not supported, etc.).
- Date/time and location are available via HA context; the assistant uses them naturally.

---

## Prompt Architecture

The final system message sent to the LLM is assembled by HA's `ChatLog.async_provide_llm_data`
and has three layers:

```
┌─────────────────────────────────────────┐
│  DEFAULT_SYSTEM_PROMPT  (this design)   │  ← Persona, tool-use rules, HA context
├─────────────────────────────────────────┤
│  HA LLM API context block               │  ← Injected by HA: entities, areas, time
├─────────────────────────────────────────┤
│  User-adjustable prompt (CONF_PROMPT)   │  ← Optional customisation from options flow
└─────────────────────────────────────────┘
```

The `DEFAULT_SYSTEM_PROMPT` constant in `const.py` is the first layer.  It establishes
the persona and the non-negotiable tool-use rule before HA appends entity context.

The `CONF_PROMPT` options field lets the user *append* personalisation (e.g. preferred
name, location-specific quirks) without replacing the foundational instructions.  The
field defaults to `DEFAULT_SYSTEM_PROMPT` in the options form so users have a reasonable
starting point.

---

## Tool-Execution Contract

The system prompt communicates three rules about tools:

1. **Use tools for device actions.**  Any request that changes state must call the
   appropriate tool.  Text confirmation is not a substitute for execution.

2. **Report the outcome, not the intent.**  After calling a tool, confirm what happened
   (e.g. "Lights are on."), not what you are about to do ("I will turn the lights on.").

3. **Admit failure honestly.**  If a tool call fails or an entity is not available, say
   so plainly.  Do not fabricate success.

---

## Persona — Detailed Rationale

The "butler" metaphor was chosen because it maps well to LLM assistant behaviour in a
smart home:

- A butler speaks only when spoken to and keeps responses efficient.
- A butler acts on instruction without requiring justification.
- A butler is *always* competent: they do not say "I cannot do that" unless it is
  genuinely impossible; otherwise they find a way.
- A butler adapts register to context — more formal in routine interactions, warmer
  when the household member is in difficulty.

This persona is intentionally not "voice assistant" (too robotic, too prone to "Sorry,
I didn't get that") and not "AI chatbot" (too verbose, too eager to explain).

---

## User-Adjustable Instructions

The `CONF_PROMPT` field in the options flow is described in the UI as
"Personalised instructions".  Its purpose is to let the user layer preferences on top
of the base persona — not to replace it.  Suggested use cases:

- Preferred address ("Always call me by my first name, Alex.")
- Household context ("We have two dogs. Morning routines start at 06:30.")
- Language preference ("Reply in German unless I write in English.")
- Tone adjustment ("You can be more casual and friendly.")

The default value shown in the form is `DEFAULT_SYSTEM_PROMPT`, giving a sensible
starting point that users can read, understand, and modify.

---

## What Not To Do

The system prompt deliberately avoids:

- Long lists of example sentences (adds tokens, rarely followed reliably).
- Instructions to "always" be helpful (models already are; redundant filler).
- Roleplay framing that invites the model to step out of character.
- Hard-coded entity names or room names (these come from HA context, not the prompt).
