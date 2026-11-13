# Security

## Reporting a problem

Please report security problems privately, not in a public issue:

- On GitHub: **Security → Report a vulnerability** in this repository, or
- By email: <hello@markos.pm>

Say what's affected, how to reproduce it, and what an attacker could do with it. Shokzer is a one-person project, so expect a first reply within a week. Once there's a fix, it's released and the report is credited to you unless you'd rather stay anonymous.

## Supported versions

Only the latest release gets fixes.

## What's in scope

- Shokzer sending something to the headset that the user didn't ask for, or that isn't documented in `docs/protocol-analysis.md`.
- Crafted replies from a Bluetooth device causing crashes, hangs or code execution in Shokzer.
- The installer (`install.sh`), the RPM or the release workflow doing something unsafe, such as fetching unverified code or writing outside the user's home folder.
- Shokzer leaking device addresses where it says they're hidden, which is any output without `--show-identifiers`.

Problems in the headset's firmware, the Shokz app or BlueZ itself belong with Shokz or the BlueZ project. If you aren't sure, report it here anyway.
