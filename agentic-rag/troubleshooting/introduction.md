[1. Introduction to the Solution and Prerequisites](../tasks/introduction.md)

### Troubleshoot the failed check, not a different layer

| Symptom | Likely issue and action |
|---|---|
| OpenSearch logs mention `vm.max_map_count` | Apply the kernel setting in the Linux environment running Podman, then restart `opensearch`. |
| Container exits with code 137 or a service repeatedly restarts | Inspect logs and available VM/host memory; increase the allocation before rerunning. |
| Port or container name already in use | Stop the earlier lab instance. For containers created by the original Bash function, deliberately remove the old named containers before starting Compose; keep their data directories. Do not run both stacks at once. |
| `opensearch-single` cannot resolve | Run the validator inside `lab`, or use `--native` with host addresses. Confirm all three services use the Compose network. |
| Dashboards is "not ready yet" | Let OpenSearch become ready first, inspect both logs, and rerun validation. A browser page loading alone does not prove its OpenSearch connection works. |
| Workspace is unwritable | The runtime uses UID 1000. Check named-volume ownership or the permissions of any optional bind mounts; do not solve this by making directories world-writable. |
| Provider HTTP 401/403 | Check the selected provider, key, project permissions, and access to the exact model. Keep the key private. |
| Provider HTTP 404 | Check the model identifier and API base URL; do not append `/chat/completions` to a base URL variable. |
| Provider HTTP 429 | Inspect quota, account billing status, or rate limits; repeated immediate retries will not repair missing quota. |
| Provider rejects `max_tokens`, `temperature`, or `top_p` | This is an application/provider request-compatibility issue, not an OpenSearch failure. The archived clients send those fields. Use a source revision with reviewed provider-aware request handling or an accepted model configuration, then rerun. The setup scripts do not rewrite agent source or silently strip parameters. |
| Provider rejects a token budget | Lower `EXTERNAL_ORCH_MAX_TOKENS` and/or `EXTERNAL_LLM_MAX_TOKENS` to supported output limits, then recreate `lab`. Context-window size is not the output limit. |
| Provider TLS or connection error | Check outbound HTTPS, DNS, proxy settings, and trusted certificate configuration. Do not disable TLS verification to bypass the error. |
| An embedding download or inference fails | Check internet access, cache disk space, package imports, and available memory; rerun `--check-embeddings`. |

After changing provider variables in the **host** `.env` file, recreate only the workspace container:

```bash
podman compose up -d --force-recreate --no-deps lab
podman compose exec lab bash
```

Container recreation terminates any running application processes and existing shells. The workspace and model cache remain in their volumes. Reopen shells and restart any later-episode services. `podman compose restart lab` does **not** apply a changed container environment.

