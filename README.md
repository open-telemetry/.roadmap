# OpenTelemetry Roadmap

This repository contains the automation for generating and maintaining the official [OpenTelemetry Roadmap project board](https://github.com/orgs/open-telemetry/projects/158).

The primary goal of this automation is to provide a centralized, high-level view of the key initiatives across the OpenTelemetry project, overcoming the limitations of GitHub's native project features which do not allow for aggregating multiple projects into a single view.

### How it Works

The automation is driven by a Python script (`scripts/sync_roadmap.py`) that runs periodically via a GitHub Action.
Here is a step-by-step breakdown of the process:

1. **Load Configuration**: The script reads the list of designated roadmap projects from the `sigs.yml` file located in the `open-telemetry/community` repository.
2. **Fetch Project Details**: For each project ID listed in `sigs.yml`, the script fetches detailed information from the source project, including its title, description, README, and the latest status update.
3. **Handle Removed Projects**: If a project ID is removed from `sigs.yml`, the script will find the corresponding item on the roadmap project board and remove it. This action does **not** close or delete the associated issue, it only removes the item from the board.
4. **Fetch Open Issues**: The script fetches all open issues in the `.roadmap` project that correspond to a roadmap project (if any).
5. **Create or Update Issues**: For each active project,
    *   If the project does not have a corresponding issue, the script creates it in this repository. A hidden HTML comment `<!-- source-project-id: ... -->` is added to the issue body to link it back to the source project.
    *   If the project is already backed by an issue, the script updates its title and body to reflect any changes from the source project.
6. **Sync with Roadmap Project Board**: For each active project,
    *   Its corresponding issue is added as an item to the central [OpenTelemetry Roadmap](https://github.com/orgs/open-telemetry/projects/158) project, unless it's already there.
    *   The custom fields on the project item (like `Status`, `Start date`, `Target date`, and `SIG`) are updated to match the latest [update](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/sharing-project-updates) from the source project.

**Please note**: The script does not currently close roadmap issues automatically, neither moves issues to the complete column. This step must be done manually.

### Running the Script Manually

While the script is designed to be automated, it can be run locally for development or testing purposes (changing `OWNER` and `OWNER_TYPE` to personal).

#### Prerequisites

*   Python 3.10+
*   pip

#### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/open-telemetry/.roadmap.git
    cd .roadmap
    ```
2.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

#### Configuration

The script is configured via constants defined at the top of `scripts/sync_roadmap.py`.
These should not need to be changed unless the source or destination repositories change.

#### Usage

The script requires a GitHub fine-grained token with the necessary permissions to be available as an environment variable.

1.  **Set the Environment Variable**:
    ```bash
    export GH_TOKEN="your_github_token"
    ```
2.  **Run the Script**:
    ```bash
    python scripts/sync_roadmap.py
    ```
3.  **Dry Run**: To see what changes the script would make without actually performing any writes (e.g., creating/updating issues, modifying project items), use the `--dry-run` flag:
    ```bash
    python scripts/sync_roadmap.py --dry-run
    ```

### GitHub Actions Integration

This script is intended to be run as a scheduled GitHub Action. The workflow file (e.g., `.github/workflows/sync_roadmap.yml`) should be configured to run the script periodically (e.g., every 6 hours).

The `GH_TOKEN` must be stored as a secret in the repository settings or at the organization level, and passed to the script in the workflow file.

### Required Permissions for `GH_TOKEN`

The GitHub token used to run the script needs the following permissions:

* **`Repository permissions`**: Scoped to `open-telemetry/.roadmap`
    * Public repository access (included by default), required to read `sigs.yml` from `open-telemetry/community`.
    * _Read_ access to metadata (required by default).
    * _Read_ and _Write_ access to issues, required to manage issues in this (`.roadmap`) repository.
* **`Organization permissions`**: Read and write access to projects.
    * Required to read source projects from the `open-telemetry` organization.
    * Required to manage the roadmap project board.

## Roadmap Item Selection

See more info in our [Roadmap Management](https://github.com/open-telemetry/community/blob/main/roadmap-management.md) documentation.
