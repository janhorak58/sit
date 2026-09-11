# Git workflow

- Never commit or push directly to `master`.
- Before changing code, create a new branch from the current `origin/master` for each requested feature or fix. Use a descriptive `feature/<slug>` or `fix/<slug>` name.
- Keep every requested feature or fix isolated to its own branch. Do not combine unrelated changes.
- Commit the completed change on that branch and open a pull request targeting `master`.
- Never merge a pull request or bypass branch protection. Only repository owner `janhorak58` may merge pull requests into `master`.
