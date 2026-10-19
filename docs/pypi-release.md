# Publish Agent Supra to PyPI

The release workflow is manually started from GitHub Actions. It tests and
builds the source distribution and wheel, checks metadata, then publishes
through PyPI Trusted Publishing. It needs no stored PyPI API token.

## First-time PyPI setup

1. Sign in to a PyPI account. If you need to register, review and accept PyPI's
   terms yourself.
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

1. Confirm `pyproject.toml` has the intended version and `main` has passed CI.
2. Open **Actions → publish SDK to PyPI → Run workflow** on `main`, enter the
   matching version, and run it. The first trusted-publisher upload creates
   the PyPI project.
3. Confirm the workflow is green and both the `.whl` and `.tar.gz` appear on
   `https://pypi.org/project/agent-supra/`.
4. In a fresh virtual environment, run `pip install agent-supra` and import
   `Client` from `agent_supra`.
5. Update the README and hosted quickstart from the GitHub install URL to
   `pip install agent-supra` only after the clean install passes.

Never publish a new build with an already-used version. Increment the version
in `pyproject.toml` before the next release.
