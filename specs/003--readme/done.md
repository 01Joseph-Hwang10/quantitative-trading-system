# Done — README

Small spec, shipped in `86a9e1b` (2026-10-01): a full project `README.md`
covering setup (`uv sync`, `.env` from `.env.example`), how to modify the
code (strategy, feeds, deployment), and usage notes (mock vs toss broker,
market-hours guards, DB sync, monitor access via SSH tunnel).

Also in the same commit: `terraform/terraform.tfvars` removed from the repo
in favor of a committed `terraform.tfvars.example`, plus a matching
`.gitignore` entry.

Follow-on README updates have accompanied later specs (deployment notes in
002, Cloud Logging in 005, `just db` in 008), so the README tracks the
current system.
