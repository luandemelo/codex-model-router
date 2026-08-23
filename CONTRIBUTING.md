# Contributing

Contributions are welcome through a focused branch and a reviewed pull
request. Please start with a clear issue or specification, keep changes
small, and add a deterministic regression test before implementation.

Run the complete local checks from the repository root:

```bash
python3 -B -m unittest discover -s skills/codex-model-router/tests -p 'test_*.py' -v
python3 -B skills/codex-model-router/scripts/check_public_release.py .
git diff --check
```

Update documentation and `CHANGELOG.md` when behavior or public interfaces
change. Do not include private evaluations, model transcripts, credentials,
local user paths, generated caches, or external service mutations in a pull
request.

## Developer Certificate of Origin

Each contribution must include a Developer Certificate of Origin sign-off in
the commit message. Add a line such as:

```text
Signed-off-by: Your Name <you@example.com>
```

By signing, you certify that you have the right to submit the contribution
under the Apache-2.0 license and understand that the contribution is public.
