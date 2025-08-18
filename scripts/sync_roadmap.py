from __future__ import annotations

import os
import re
import sys
import yaml
import base64
import argparse
import logging
from pathlib import Path
from github import Github, Issue
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

# Config
OWNER = "open-telemetry"
OWNER_TYPE = "organization"
SIGS_FILE_REPO = f"{OWNER}/community"
SIGS_FILE_PATH = "sigs.yml"
ROADMAP_PROJECT_ID = 158
ROADMAP_REPO = f"{OWNER}/.roadmap"
STATUS_TO_FIELD_MAP = {
    "ON_TRACK": "On track",
    "AT_RISK": "At risk",
    "OFF_TRACK": "Off track",
    "COMPLETE": "Complete",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("gql").setLevel(logging.WARNING)


class RoadmapManager:
    def __init__(self, github_token: str, dry_run: bool = False):
        self.dry_run = dry_run
        self.graphql_client = self._create_graphql_client(github_token)
        self.github_client = Github(github_token)
        self.queries = self._load_all_queries()

        project_details = self._get_project_details_by_number(OWNER, ROADMAP_PROJECT_ID, owner_type=OWNER_TYPE)
        if not project_details:
            raise ValueError(f"Could not find roadmap project with ID {ROADMAP_PROJECT_ID}")
        self.roadmap_project_node_id = project_details["id"]
        self.roadmap_repo = self.github_client.get_repo(ROADMAP_REPO)
        self.roadmap_fields = self._get_project_fields(self.roadmap_project_node_id)

    @staticmethod
    def _load_all_queries() -> dict[str, str]:
        """Loads all GraphQL queries from the graphql directory."""
        queries = {}
        query_dir = Path(__file__).parent / "graphql"
        for query_path in query_dir.glob("*.graphql"):
            with open(query_path, "r") as f:
                queries[query_path.stem] = f.read()
        return queries

    @staticmethod
    def _create_graphql_client(github_token: str) -> Client:
        transport = RequestsHTTPTransport(
            url="https://api.github.com/graphql",
            headers={"Authorization": f"Bearer {github_token}"},
            use_json=True,
        )
        return Client(transport=transport, fetch_schema_from_transport=True)

    @staticmethod
    def _build_issue_body(project_details: dict) -> str:
        """Constructs the body for a roadmap issue."""
        short_description = project_details.get("shortDescription") or "No short description provided."
        readme = project_details.get("readme") or "No README provided."
        project_id_comment = f"<!-- source-project-id: {project_details['id']} -->"
        source_project_url = f"https://github.com/orgs/{OWNER}/projects/{project_details['project_number']}"

        return (
            f"## {source_project_url}\n\n"
            f"{short_description}\n\n"
            f"## README\n\n{readme}\n\n"
            f"{project_id_comment}"
        )

    @staticmethod
    def _get_project_node_id_from_issue_body(issue_body: str) -> str | None:
        """Extracts the project node ID from the issue body."""
        if not issue_body:
            return None
        match = re.search(r"<!-- source-project-id: (.*) -->", issue_body)
        return match.group(1) if match else None

    @staticmethod
    def _get_project_item_for_issue(issue: Issue, roadmap_items: list[dict]) -> dict | None:
        """Finds the associated item in the roadmap project for a given issue."""
        for item in roadmap_items:
            if item.get("content", {}).get("id") == issue.node_id:
                return item
        return None

    def _get_project_details_by_number(
        self, owner: str, project_number: int, owner_type: str = "organization"
    ) -> dict | None:
        """Gets the project details for a project from an owner and project number."""
        logging.info(f"Getting details for project {project_number} in {owner}...")
        query_name = f"get_project_details_by_number_{owner_type}"
        query = gql(self.queries[query_name])
        variables = {"login" if owner_type == "user" else "org": owner, "project_number": project_number}

        response = self.graphql_client.execute(query, variable_values=variables)

        if response and response.get(owner_type) and response[owner_type].get("projectV2"):
            project_details = response[owner_type]["projectV2"]
            project_details["project_number"] = project_number
            return project_details
        return None

    def _get_project_fields(self, project_node_id: str) -> dict:
        """Get the custom fields and their options for a project."""
        query = gql(self.queries["get_project_fields"])
        response = self.graphql_client.execute(query, variable_values={"project_node_id": project_node_id})
        fields = {}
        if response.get("node", {}).get("fields", {}).get("nodes"):
            for field in response["node"]["fields"]["nodes"]:
                field_data = {"id": field["id"], "type": field["dataType"]}
                if "options" in field:
                    field_data["options"] = {opt["name"]: opt["id"] for opt in field["options"]}
                fields[field["name"]] = field_data
        return fields

    def get_roadmap_issues(self) -> dict[str, dict]:
        """Fetches all issues from the roadmap repository that are linked to a source project."""
        logging.info("Fetching current roadmap issues...")
        roadmap_items = {}
        for issue in self.roadmap_repo.get_issues(state="open"):
            if issue.body and (project_node_id := self._get_project_node_id_from_issue_body(issue.body)):
                roadmap_items[project_node_id] = {"issue": issue}
        logging.info(f"Found {len(roadmap_items)} issues linked to a source project.")
        return roadmap_items

    def _create_or_update_issue(self, project_details: dict, issue: "Issue | None" = None) -> "Issue | None":
        """Creates or updates an issue for a given project."""
        issue_title = project_details["title"]
        issue_body = self._build_issue_body(project_details)

        if issue:
            if issue.title == issue_title and issue.body == issue_body:
                logging.info(f"No changes to issue for project {project_details['title']}")
                return issue

            if self.dry_run:
                logging.info(f"[DRY RUN] Would update issue for project {project_details['title']}")
            else:
                issue.edit(title=issue_title, body=issue_body)
                logging.info(f"Updated issue for project {project_details['title']}")
        else:
            if self.dry_run:
                logging.info(f"[DRY RUN] Would create issue for project {project_details['title']}")
                return None
            issue = self.roadmap_repo.create_issue(title=issue_title, body=issue_body)
            logging.info(f"Created issue for project {project_details['title']}")
        return issue

    def _add_issue_to_roadmap_project(self, issue: Issue, project_details: dict) -> dict | None:
        """Adds an issue to the roadmap project."""
        if self.dry_run:
            logging.info(f"[DRY RUN] Would add issue for project {project_details['title']} to roadmap project.")
            return None

        query = gql(self.queries["add_project_item_by_issue_id"])
        variables = {"project_id": self.roadmap_project_node_id, "content_id": issue.node_id}
        response = self.graphql_client.execute(query, variable_values=variables)
        logging.info(f"Added issue for project {project_details['title']} to roadmap project.")
        return response["addProjectV2ItemById"]["item"]

    def _update_roadmap_fields(self, project_item_details: dict | None, project_details: dict, sig_name: str) -> None:
        """Updates the custom fields in the roadmap project for a given item."""
        if not project_item_details:
            logging.info("No project item details available to update.")
            return

        # Prepare new values
        status_updates = project_details.get("latestStatusUpdate", {}).get("nodes", [])
        latest_status_update = status_updates[0] if status_updates else {}
        api_status = latest_status_update.get("status")
        human_readable_status = STATUS_TO_FIELD_MAP.get(api_status)
        status_option_id = (
            self.roadmap_fields["Status"]["options"].get(human_readable_status) if human_readable_status else None
        )

        # Check if an update is needed
        if all(
            [
                (
                    human_readable_status == project_item_details["status"]["name"]
                    if project_item_details.get("status")
                    else None
                ),
                (
                    latest_status_update.get("startDate") == project_item_details["startDate"]["date"]
                    if project_item_details.get("startDate")
                    else None
                ),
                (
                    latest_status_update.get("targetDate") == project_item_details["targetDate"]["date"]
                    if project_item_details.get("targetDate")
                    else None
                ),
                sig_name == project_item_details["sig"]["text"] if project_item_details.get("sig") else None,
            ]
        ):
            logging.info(f"No changes to roadmap fields for item {project_item_details["id"]}")
            return

        variables = {
            "projectId": self.roadmap_project_node_id,
            "itemId": project_item_details["id"],
            "statusFieldId": self.roadmap_fields["Status"]["id"],
            "statusValue": status_option_id,
            "startDateFieldId": self.roadmap_fields["Start date"]["id"],
            "startDateValue": latest_status_update.get("startDate"),
            "targetDateFieldId": self.roadmap_fields["Target date"]["id"],
            "targetDateValue": latest_status_update.get("targetDate"),
            "sigFieldId": self.roadmap_fields["SIG"]["id"],
            "sigValue": sig_name,
        }

        if self.dry_run:
            logging.info(f"[DRY RUN] Would update fields for item {project_item_details["id"]}")
            return

        query = gql(self.queries["update_project_item_fields"])
        self.graphql_client.execute(query, variable_values=variables)
        logging.info(f"Updated fields for item {project_item_details["id"]}.")

    def _sync_project(
        self, project_details: dict, roadmap_items: list, sig_name: str, issue: "Issue | None" = None
    ) -> None:
        """Syncs a single project to an issue in the roadmap repository."""
        logging.info(f"Syncing project '{project_details['title']}' to roadmap...")
        issue = self._create_or_update_issue(project_details, issue)
        if not issue:
            logging.warning(f"Could not create or update issue for project {project_details['title']}")
            return

        project_item_details = self._get_project_item_for_issue(issue, roadmap_items)
        if not project_item_details:
            project_item_details = self._add_issue_to_roadmap_project(issue, project_details)

        self._update_roadmap_fields(project_item_details, project_details, sig_name)

    def get_sigs_projects(self) -> dict[str, list[dict]]:
        """Gets sigs.yml and returns a dictionary of SIGs and their project details."""
        logging.info("Getting sigs.yml file from community repository...")
        repo = self.github_client.get_repo(SIGS_FILE_REPO)
        content = repo.get_contents(SIGS_FILE_PATH, ref="refs/pull/2911/head")
        sigs_yaml = yaml.safe_load(base64.b64decode(content.content))

        sigs_projects = {}
        for sig_group in sigs_yaml:
            for sig in sig_group.get("sigs", []):
                sig_name = sig.get("name")
                project_details_list = []
                for project_number in sig.get("roadmapProjectIDs", []):
                    if project_number and (
                        details := self._get_project_details_by_number(OWNER, project_number, OWNER_TYPE)
                    ):
                        project_details_list.append(details)
                    else:
                        logging.warning(f"Could not find project with ID {project_number} in org {OWNER}")

                if sig_name and project_details_list:
                    sigs_projects[sig_name] = project_details_list
        return sigs_projects

    def sync_projects_from_sigs(
        self, sigs_projects: dict[str, list[dict]], roadmap_issues: dict, roadmap_items: list
    ) -> None:
        """Syncs all projects from sigs.yml to the roadmap."""
        logging.info("Syncing projects from sigs.yml...")
        for sig_name, project_details_list in sigs_projects.items():
            for project_details in project_details_list:
                project_node_id = project_details["id"]
                existing_issue = roadmap_issues.get(project_node_id, {}).get("issue")
                self._sync_project(project_details, roadmap_items, sig_name, existing_issue)

    def remove_old_items_from_project(self, sigs_projects: dict[str, list[dict]], roadmap_items: list) -> list:
        """Removes items from the roadmap project if they are no longer in sigs.yml.
        Returns the remaining, active items."""
        logging.info("Checking for removed projects...")
        active_project_node_ids = {details["id"] for projects in sigs_projects.values() for details in projects}

        items_to_remove = []
        items_to_keep = []
        for item in roadmap_items:
            body = item.get("content", {}).get("body", "")
            if (node_id := self._get_project_node_id_from_issue_body(body)) and node_id not in active_project_node_ids:
                items_to_remove.append(item)
            else:
                items_to_keep.append(item)

        if not items_to_remove:
            logging.info("No projects to remove.")
            return items_to_keep

        delete_query = gql(self.queries["delete_project_item_by_item_id"])
        for item in items_to_remove:
            item_id = item["id"]
            issue_url = item.get("content", {}).get("url", "N/A")
            if self.dry_run:
                logging.info(f"[DRY RUN] Would remove item {item_id} (for issue {issue_url})")
            else:
                variables = {"project_id": self.roadmap_project_node_id, "item_id": item_id}
                self.graphql_client.execute(delete_query, variable_values=variables)
                logging.info(f"Removed item {item_id} (for issue {issue_url}) from roadmap project.")
        return items_to_keep

    def get_roadmap_project_items(self) -> list:
        """Fetches all items from the roadmap project, handling pagination."""
        logging.info("Fetching roadmap project items...")
        items = []
        after_cursor = None
        query = gql(self.queries["get_roadmap_items"])

        while True:
            variables = {"roadmap_project_node_id": self.roadmap_project_node_id, "after": after_cursor}
            response = self.graphql_client.execute(query, variable_values=variables)

            if not (node := response.get("node")) or not (item_data := node.get("items")):
                break

            items.extend(item_data["nodes"])
            if not item_data["pageInfo"]["hasNextPage"]:
                break
            after_cursor = item_data["pageInfo"]["endCursor"]

        return items


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Sync OpenTelemetry roadmap projects to issues.")
    parser.add_argument("--dry-run", action="store_true", help="Run the script without making any changes.")
    args = parser.parse_args()

    if not (github_token := os.environ.get("GH_TOKEN")):
        logging.error("GH_TOKEN environment variable is not set")
        sys.exit(1)

    try:
        manager = RoadmapManager(github_token, args.dry_run)
        sigs_projects = manager.get_sigs_projects()

        roadmap_items = manager.get_roadmap_project_items()
        active_roadmap_items = manager.remove_old_items_from_project(sigs_projects, roadmap_items)

        roadmap_issues = manager.get_roadmap_issues()
        manager.sync_projects_from_sigs(sigs_projects, roadmap_issues, active_roadmap_items)
    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
