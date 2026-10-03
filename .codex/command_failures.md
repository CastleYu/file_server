# Command failure notes

## 2026-07-14

- Symptom: sandboxed PowerShell process creation failed with Windows error 1920 while reading a local skill file.
- Classification: environment/sandbox issue, not a business or server failure.
- Reuse: rerun the same read-only PowerShell command with the controlled elevated execution path; it succeeded.

- Symptom: `H:\\Documents\\ssh\\scripts\\Invoke-Server.ps1` could not find `config\\aliyun-root.json`.
- Classification: local SSH helper configuration is missing, not a remote-server failure.
- Reuse: for this task, use the user-supplied credentials through a one-time, read-only Paramiko connection; restore the helper config separately before relying on that reusable path.

- Symptom: the `admin` account on `192.168.3.11` cannot read the PostgreSQL data directory or run passwordless `sudo -u postgres psql`.
- Classification: remote privilege boundary, not a PostgreSQL runtime failure.
- Reuse: capacity/configuration queries must run as `root` or `postgres`; do not infer exact database size or alter PostgreSQL settings from the `admin` account.
