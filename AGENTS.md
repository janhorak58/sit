# Git workflow

- Never commit or push directly to `dev` or `master`. Both branches are protected: they only change through a merged pull request.
- `dev` is the integration branch and the default base. `master` is the release branch, updated only by the repository owner merging `dev` into it.
- Before changing code, create a new branch from the current `origin/dev` for each requested feature or fix. Use a descriptive `feature/<slug>` or `fix/<slug>` name.
- Keep every requested feature or fix isolated to its own branch. Do not combine unrelated changes.
- Commit the completed change on that branch and open a pull request targeting `dev`. Never target `master`.
- Never merge a pull request or bypass branch protection. Only repository owner `janhorak58` merges pull requests.
