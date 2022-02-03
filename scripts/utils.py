import copy
import json
import pathlib
import warnings
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from marshmallow import missing
from ruamel.yaml import YAML, comments

from bare_utils import SOURCE_BASE_URL, set_gh_actions_output, set_gh_actions_outputs
from bioimageio.spec import load_raw_resource_description, serialize_raw_resource_description_to_dict
from bioimageio.spec.shared.utils import resolve_source

set_gh_actions_output = set_gh_actions_output
set_gh_actions_outputs = set_gh_actions_outputs


# todo: use MyYAML from bioimageio.spec. see comment below
class MyYAML(YAML):
    """add convenient improvements over YAML
    improve dump:
        - make sure to dump with utf-8 encoding. on windows encoding 'windows-1252' may otherwise be used
        - expose indentation kwargs for dump
    """

    def dump(self, data, stream=None, *, transform=None):
        if isinstance(stream, pathlib.Path):
            with stream.open("wt", encoding="utf-8") as f:
                return super().dump(data, f, transform=transform)
        else:
            return super().dump(data, stream, transform=transform)


# todo: clean up difference to bioimageio.spec.shared.yaml (diff is typ='safe'), but with 'safe' enforce_block_style does not work
yaml = MyYAML()


def iterate_over_gh_matrix(matrix: Union[str, Dict[str, list]]):
    if isinstance(matrix, str):
        matrix = json.loads(matrix)

    assert isinstance(matrix, dict), matrix
    if "exclude" in matrix:
        raise NotImplementedError("matrix:exclude")

    elif "include" in matrix:
        if len(matrix) > 1:
            raise NotImplementedError("matrix:include with other keys")

        yield from matrix["include"]

    else:
        keys = list(matrix)
        for vals in product(*[matrix[k] for k in keys]):
            yield dict(zip(keys, vals))


def resolve_partners(
    rdf: dict, *, current_format: str, previous_partner_collections: Dict[str, dict]
) -> Tuple[List[dict], List[dict], Dict[str, dict], set]:
    from bioimageio.spec import load_raw_resource_description
    from bioimageio.spec.collection.v0_2.raw_nodes import Collection
    from bioimageio.spec.collection.v0_2.utils import resolve_collection_entries

    partners = []
    updated_partner_resources = []
    updated_partner_collections = {}
    ignored_partners = set()
    if "partners" in rdf["config"]:
        partners = copy.deepcopy(rdf["config"]["partners"])
        for idx in range(len(partners)):
            partner = partners[idx]
            try:
                partner_collection = load_raw_resource_description(partner["source"], update_to_format=current_format)
                assert isinstance(partner_collection, Collection)
            except Exception as e:
                warnings.warn(
                    f"Invalid partner source {partner['source']} (Cannot update to format {current_format}): {e}"
                )
                ignored_partners.add(f"partner[{idx}]")
                continue

            partner_id: str = partner.get("id") or partner_collection.id
            if not partner_id:
                warnings.warn(f"Missing partner id for partner {idx}: {partner}")
                ignored_partners.add(f"partner[{idx}]")
                continue

            serialized_partner_collection = serialize_raw_resource_description_to_dict(partner_collection)

            # option to skip based on partner collection diff
            # if serialized_partner_collection == previous_partner_collections.get(partner_id):
            #     continue  # no change in partner collection

            updated_partner_collections[partner_id] = serialized_partner_collection
            if partner_collection.config:
                partners[idx].update(partner_collection.config)

            partners[idx]["id"] = partner_id

            for entry_idx, (entry_rdf, entry_error) in enumerate(
                resolve_collection_entries(partner_collection, collection_id=partner_id)
            ):
                if entry_error:
                    warnings.warn(f"{partner_id}[{entry_idx}]: {entry_error}")
                    continue

                # Convert relative links to absolute  # todo: move to resolve_collection_entries
                if "links" in entry_rdf:
                    for idx, link in enumerate(entry_rdf["links"]):
                        if "/" not in link:
                            entry_rdf["links"][idx] = partner_id + "/" + link

                updated_partner_resources.append(
                    dict(
                        status="accepted",
                        id=entry_rdf["id"],
                        type=entry_rdf.get("type", "unknown"),
                        versions=[
                            dict(
                                name=entry_rdf.get("name", "unknown"),
                                version_id="latest",
                                version_name="latest",
                                status="accepted",
                                rdf_source=entry_rdf,
                            )
                        ],
                    )
                )

    return partners, updated_partner_resources, updated_partner_collections, ignored_partners


def update_resource_rdfs(dist: Path, resource: dict) -> Dict[str, Any]:
    """write an updated rdf per version to dist for the given resource"""
    from imjoy_plugin_parser import get_plugin_as_rdf

    resource_id = resource["id"]
    updated_version_rdfs = {}
    for version_info in resource["versions"]:
        if version_info["status"] == "blocked":
            continue

        # Ignore the name in the version info
        del version_info["name"]

        invalid_source = False
        if isinstance(version_info["rdf_source"], dict):
            if version_info["rdf_source"].get("source", "").split("?")[0].endswith(".imjoy.html"):
                rdf_info = dict(get_plugin_as_rdf(resource["id"].split("/")[1], version_info["rdf_source"]["source"]))
            else:
                rdf_info = {}

            # Inherit the info from e.g. the collection
            this_version = version_info["rdf_source"].copy()
            this_version.update(rdf_info)
            assert missing not in this_version.values(), this_version
        elif version_info["rdf_source"].split("?")[0].endswith(".imjoy.html"):
            this_version = dict(get_plugin_as_rdf(resource["id"].split("/")[1], version_info["rdf_source"]))
            assert missing not in this_version.values(), this_version
        else:
            try:
                rdf_node = load_raw_resource_description(version_info["rdf_source"])
            except Exception as e:
                warnings.warn(f"Failed to interpret {version_info['rdf_source']} as rdf: {e}")
                try:
                    this_version = resolve_source(version_info["rdf_source"])
                    if not isinstance(this_version, dict):
                        raise TypeError(type(this_version))
                except Exception as e:
                    this_version = {
                        "invalid_original_rdf_source": version_info["rdf_source"],
                        "invalid_original_rdf_source_error": str(e),
                    }
            else:
                this_version = serialize_raw_resource_description_to_dict(rdf_node)

        if "config" not in this_version:
            this_version["config"] = {}
        if "bioimageio" not in this_version["config"]:
            this_version["config"]["bioimageio"] = {}

        # Allowing to override fields
        for k in version_info:
            # Place these fields under config.bioimageio
            if k in ["created", "doi", "status", "version_id", "version_name"]:
                this_version["config"]["bioimageio"][k] = version_info[k]
            else:
                this_version[k] = version_info[k]

        if "rdf_source" in this_version and isinstance(this_version["rdf_source"], dict):
            del this_version["rdf_source"]

        if "owners" in resource:
            this_version["config"]["bioimageio"]["owners"] = resource["owners"]

        this_version["rdf_source"] = f"{SOURCE_BASE_URL}/rdfs/{resource_id}/{version_info['version_id']}/rdf.yaml"

        v_deploy_path = dist / "rdfs" / resource_id / version_info["version_id"] / "rdf.yaml"
        v_deploy_path.parent.mkdir(parents=True, exist_ok=True)
        yaml.dump(this_version, v_deploy_path)

        updated_version_rdfs[version_info["version_id"]] = v_deploy_path

    return updated_version_rdfs


def enforce_block_style(data):
    """enforce block style in yaml data dump. Does not work with YAML(typ='safe')"""
    if isinstance(data, list):
        converted = comments.CommentedSeq([enforce_block_style(d) for d in data])
    elif isinstance(data, dict):
        converted = comments.CommentedMap({enforce_block_style(k): enforce_block_style(v) for k, v in data.items()})
    else:
        return data

    converted.fa.set_block_style()
    return converted
