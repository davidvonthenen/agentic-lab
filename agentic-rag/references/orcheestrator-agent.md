[Task 4: Orchestrator Expert](../tasks/orcheestrator-agent.md)

## Source map

These are the implementation files used by this task. When the overview and code differ, follow the current code and rerun the relevant local probe.

| File | Responsibility |
|---|---|
| [`Makefile`](../scripts/orcheestrator-agent/Makefile) | Service startup and audit-verification targets. |
| [`src/host_agent/__main__.py`](../scripts/orcheestrator-agent/src/host_agent/__main__.py) | HTTP input contract, session-label handling, and response construction. |
| [`src/host_agent/config.py`](../scripts/orcheestrator-agent/src/host_agent/config.py) | Effective settings, model-profile selection, and fixed workflow limits. |
| [`src/host_agent/models.py`](../scripts/orcheestrator-agent/src/host_agent/models.py) | Typed routing, completion, and expert-response contracts. |
| [`src/host_agent/policy_manager.py`](../scripts/orcheestrator-agent/src/host_agent/policy_manager.py) | Domain assessment, company continuity, and initial-plan validation. |
| [`src/host_agent/llm_client.py`](../scripts/orcheestrator-agent/src/host_agent/llm_client.py) | Planning/completion payloads, combined synthesis, and bounded model retries. |
| [`src/host_agent/remote_agent_connection.py`](../scripts/orcheestrator-agent/src/host_agent/remote_agent_connection.py) | A2A delegation, response normalization, and context-continuation recovery. |
| [`src/host_agent/routing_agent.py`](../scripts/orcheestrator-agent/src/host_agent/routing_agent.py) | Fixed execution phases, canonical delegate requests, composition, checks, and audit records. |
| [`src/host_agent/audit.py`](../scripts/orcheestrator-agent/src/host_agent/audit.py) | Hash-linked append and chain verification. |
