# Git history cleanup

The current repository tree no longer contains the bundled FFmpeg binaries, MobileNet-SSD weights, or committed generated `ok/` output. Those paths are also ignored for future work.

Older Git commits still contain the historical binary blobs. Removing them requires a coordinated history rewrite (for example with `git filter-repo`) followed by a force push and a fresh clone for other contributors. This is intentionally **not** performed by an automated hardening change because it rewrites every affected commit and can invalidate existing clones, tags, and open branches.

Before performing the migration:

1. Announce a repository freeze and confirm there are no important unpublished branches.
2. Create a verified backup/mirror of the repository.
3. Rewrite history to remove `.tools/`, `.models/`, and generated output/binary paths.
4. Run the full test suite against the rewritten default branch.
5. Force-update the remote and ask all contributors to reclone.

Large runtime assets should live outside Git (release assets, object storage, or a dedicated artifact store). Git LFS is appropriate only when binary versioning inside Git is genuinely required.
