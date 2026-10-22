# Security policy

## Supported versions

myHA is pre-alpha. Only the latest commit on `main` and, once releases exist, the latest release
receive security fixes. Older versions are not patched.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Use GitHub's private vulnerability reporting: on the repository's **Security** tab, select
  **Report a vulnerability**.
- If that option is not available, write to the maintainer address in
  [`repository.yaml`](repository.yaml).

Include the affected version or commit, the steps to reproduce and the impact you expect.

## What to expect

- An acknowledgement within 7 days.
- A first assessment within 14 days, with a plan or the reasons if the report is not accepted.
- A fix in the next release for confirmed issues, and a published advisory once the fix is
  available. Please keep the issue private until then.

The security design is described in [docs/security.md](docs/security.md).
