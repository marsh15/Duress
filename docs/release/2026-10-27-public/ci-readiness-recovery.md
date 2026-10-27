# Focused CI PostgreSQL readiness recovery

The documentation/media head `3f33cab` encountered a reusable external-check failure in **Start isolated PostgreSQL on loopback**, before the caller application or Duress gate ran. The previous merge run on identical runtime source passed. Exit 2 occurred immediately after image/container startup, not during a Duress business-state test.

A single fresh local boot of the same official `postgres:16-alpine` image reproduced the readiness race: at 2.417 seconds the default Unix-socket probe returned 0 while the TCP loopback probe returned 2; socket readiness then disappeared during initialization-server shutdown, and the final TCP server became ready at 2.986 seconds. [Recorded observation](postgres-readiness-probe.json). No Duress experiment or real credentials were used in this reproduction.

Both CI workflows now use `pg_isready -h 127.0.0.1 -p 5432` inside the existing bounded readiness loop and its final assertion. This prevents the image's socket-only temporary initialization server from satisfying acceptance readiness. Loop counts, failure handling, business checks, sanitization and evidence uploads remain unchanged. No runtime/UI/oracle code changed and no check was bypassed. The correction requires its own current-head GitHub CI; earlier results are not relabelled as a pass for that head.
