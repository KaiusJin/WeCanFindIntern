# Native desktop resources

The packaging pipeline places native runtime bundles here before Electron Forge runs:

```text
backend/<platform>-<arch>/wecanfindintern-backend/wecanfindintern-backend[.exe]
postgres/<platform>-<arch>/{bin,lib,share}
```

PostgreSQL bundles must include PostgreSQL 16 plus the `vector`, `pgcrypto`, and
`pg_trgm` extensions. They are deliberately not committed to Git.
