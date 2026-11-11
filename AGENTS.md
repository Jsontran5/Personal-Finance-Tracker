# Repository workflow

- Keep `context_docs/` and `context_folder/` local-only. Read and update their checkpoints when present, but never stage, force-add, commit, or push their contents.
- Make small, frequent commits after verified logical changes. Keep unrelated user edits out of commits. Never commit credentials, financial data, runtime files, or backups.
- The repository has local commit/push hooks protecting context folders. Preserve those hooks. Do not rewrite Git history without user authorization.
- For frontend changes, render and visually inspect both themes at desktop and narrow widths, including long account lists and expanded/collapsed details. Record verification in the local project state.
