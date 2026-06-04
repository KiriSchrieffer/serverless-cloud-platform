# Threat Model

This project uses Docker for local educational process and resource isolation.
It should not be described as production-grade multi-tenant sandboxing.

Important MVP rules:

- Never mount the host Docker socket into user-function containers.
- Run function containers as a non-root user.
- Disable networking by default.
- Use a read-only root filesystem with a writable `/tmp`.
- Drop Linux capabilities.
- Enable `no-new-privileges`.
- Enforce CPU, memory, process count, payload, result, and log limits.
