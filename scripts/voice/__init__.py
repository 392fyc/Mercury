# -*- coding: utf-8 -*-
"""Mercury voice — 双向中文语音 agent 交互 (Issue #468).

A detachable package layering on top of #465's voice-zh-input:
  stt        — faster-whisper STT engine + single-utterance blocking capture
  tts        — Kokoro-FastAPI (OpenAI-compatible) client + edge-tts fallback
  state      — secretary/work dual-mode state machine + secretary note-taking
  mcp_server — stdio MCP server exposing listen / announce / set_mode / record_note

ADR: .mercury/docs/research/issue-468-voice-agent-adr-2026-06.md
"""
__all__ = ["stt", "tts", "state"]
