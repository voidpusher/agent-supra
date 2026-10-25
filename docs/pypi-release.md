# Publish Agent Supra to PyPI

The release workflow is manually started from GitHub Actions. It tests and
builds the source distribution and wheel, checks metadata, then publishes
through PyPI Trusted Publishing. It needs no stored PyPI API token.

## First-time PyPI setup

1. Sign in to a PyPI account. If you need to register, review and accept PyPI's
   terms yourself. PyPI requires a verified primary email and two-factor
   authentication before opening the Publishing settings. Generate recovery
   codes, store them privately, and complete 2FA setup in your account.
2. In PyPI account **Publishing**, add a **pending GitHub publisher** for:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `agent-supra` |
   | Owner | `voidpusher` |
   | Repository | `agent-supra` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A pending publisher does not reserve the name. Publish promptly after
   configuring it. If a PyPI project already exists under your account, add
   the same publisher under that project's Publishing settings instead.
3. In GitHub, create an environment named `pypi` for this repository. Restrict
   deployment branches to `main` and require an owner review if those controls
   are available for the repository.

## Release

1. Set the intended version in both `pyproject.toml` and
   `agent_supra/__init__.py`, update `CHANGELOG.md`, and confirm `main` passes CI.
2. Open **Actions → publish SDK to PyPI → Run workflow** on `main`, enter the
   matching version, and run it. The first trusted-publisher upload creates
   the PyPI project.
3. Confirm the workflow is green and both the `.whl` and `.tar.gz` appear on
   `https://pypi.org/project/agent-supra/`.
4. In a fresh virtual environment outside the checkout, install the exact new
   version from PyPI, import `Client`, and check `agent-supra watch --help`,
   `agent-supra claude-hook --help`, and `agent-supra proxy --help`. Verify the
   core install has no mandatory dependencies. Separately install the `proxy`
   extra and confirm it resolves successfully.
5. Confirm the README and hosted quickstart offer `pip install agent-supra`
   after the clean install passes.

Never publish a new build with an already-used version. Increment the version
in `pyproject.toml` before the next release.
