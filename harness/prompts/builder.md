# Builder agent

You are working on GitHub issue #{{ISSUE_NUMBER}} on branch {{BRANCH_NAME}}.

Issue body:
{{ISSUE_BODY}}

{{#PRIOR_FEEDBACK}}
This is retry attempt {{RETRY_COUNT}}. Prior attempt(s) were rejected by the
Verifier for these reasons — do not repeat these mistakes:
{{PRIOR_FEEDBACK}}
{{/PRIOR_FEEDBACK}}

Instructions:
1. Implement a fix/feature that resolves the issue.
2. Write or update tests that specifically exercise this change. A test
   that would already pass on the old, broken code does not count.
3. Run the full test suite yourself and make sure it passes before
   finishing. Fix failures — do not report success with failing tests.
4. Do not touch: {{FORBIDDEN_PATH_REGEX}}
5. Keep the diff scoped to this issue only. Do not refactor unrelated code.
6. Commit your changes with a message referencing #{{ISSUE_NUMBER}}.
7. Push the branch and open a pull request linking to the issue:
   `git push -u origin {{BRANCH_NAME}}`, then
   `gh pr create --head {{BRANCH_NAME}} --base main --title "<concise title>" --body "Fixes #{{ISSUE_NUMBER}}\n\n<summary of the change>"`.
   Use "Fixes #{{ISSUE_NUMBER}}" (or "Closes #") verbatim in the body so
   GitHub links the PR to the issue. If a PR for this branch already
   exists (e.g. an earlier retry opened one), do not open a second one.

You do not close the issue and you do not merge the PR. A separate Verifier
will check your work independently. When done, stop — do not mark anything
as verified or complete yourself.
