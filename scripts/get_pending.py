from pathlib import Path

import requests
import typer
from ruamel.yaml import YAML

from utils import set_gh_actions_outputs

yaml = YAML(typ="safe")
MAIN_BRANCH_URL = "https://raw.githubusercontent.com/bioimage-io/collection-bioimage-io/main"


def main(
    collection_dir: Path,
    branch: str = typer.Argument(
        ..., help="branch name should be 'auto-update-{resource_id} and is only used to get resource_id."
    ),
):
    if branch.startswith("auto-update-"):
        resource_id = branch[len("auto-update-") :]
        resource_path = collection_dir / resource_id / "resource.yaml"
        response = requests.get(f"{MAIN_BRANCH_URL}/{resource_path}")
        if response.ok:
            previous_resource = yaml.load(response.text)
            previous_versions = {v["version_id"]: v for v in previous_resource["versions"]}
        else:
            previous_resource = None
            previous_versions = None
        resource = yaml.load(resource_path)
        # status of the entire resource item has changed
        if previous_resource and previous_resource.get("status") != resource.get("status"):
            pending_versions = resource["versions"]
        else:
            pending_versions = []
            for v in resource["versions"]:
                previous_version = previous_versions and previous_versions.get(v["version_id"])
                if previous_version is None or previous_version != v:  # check changes
                    pending_versions.append(v["version_id"])

        out = dict(
            pending_matrix=dict(include=[{"resource_id": resource_id, "version_id": vid} for vid in pending_versions]),
            has_pending_matrix=bool(pending_versions),
        )
    else:
        # don't fail, but warn for non-auto-update branches
        print(f"called with non-auto-update branch {branch}")
        out = dict(pending_matrix="", found_pending=False)

    set_gh_actions_outputs(out)
    return out


if __name__ == "__main__":
    typer.run(main)
