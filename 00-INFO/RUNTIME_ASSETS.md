# Runtime assets policy

The application expects runtime tools, generated media, model weights, knowledge data, uploads,
and SQLite state to live outside source control. `.gitignore` and `.dockerignore` exclude these
paths for future work.

The project image installs FFmpeg from the base distribution rather than copying a repository-local
FFmpeg bundle.

For an existing clone, remove any previously tracked runtime bundle with a deliberate Git history
cleanup after taking a mirror backup. This branch does not rewrite history automatically.
