import json
import subprocess
from dataclasses import dataclass

from openshift_infra_deploy.subprocess_util import run_cmd


@dataclass(frozen=True)
class AwsClusterContext:
    cluster_name: str
    region: str
    availability_zone: str
    infra_id: str
    ami_id: str


def _oc_jsonpath(args: list[str]) -> str:
    p = run_cmd(["oc", *args], capture_output=True)
    return (p.stdout or "").strip()


def _try_oc_jsonpath(args: list[str]) -> str:
    """Like _oc_jsonpath but returns \"\" if oc fails (e.g. empty .items[0] jsonpath)."""
    p = run_cmd(["oc", *args], capture_output=True, check=False)
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip()


def gather_aws_cluster_context(availability_zone: str | None = None) -> AwsClusterContext:
    cluster_name = _oc_jsonpath(
        ["get", "infrastructure", "cluster", "-o", "jsonpath={.status.infrastructureName}"]
    )
    region = _oc_jsonpath(
        [
            "get",
            "infrastructure",
            "cluster",
            "-o",
            "jsonpath={.status.platformStatus.aws.region}",
        ]
    )
    infra_id = _oc_jsonpath(
        ["get", "infrastructure", "cluster", "-o", "jsonpath={.status.infrastructureName}"]
    )

    if availability_zone is not None:
        az = availability_zone.strip()
        if not az:
            raise RuntimeError("availability_zone override is empty after stripping whitespace")
        print("  Using availability zone from CLI/env (not inferring from worker machines).")
    else:
        az = _try_oc_jsonpath(
            [
                "get",
                "machines",
                "-n",
                "openshift-machine-api",
                "-l",
                "machine.openshift.io/cluster-api-machine-role=worker",
                "-o",
                "jsonpath={.items[0].spec.providerSpec.value.placement.availabilityZone}",
            ]
        )
        if not az:
            print("  No worker machines found, using master node configuration...")
            az = _try_oc_jsonpath(
                [
                    "get",
                    "machines",
                    "-n",
                    "openshift-machine-api",
                    "-l",
                    "machine.openshift.io/cluster-api-machine-role=master",
                    "-o",
                    "jsonpath={.items[0].spec.providerSpec.value.placement.availabilityZone}",
                ]
            )

    ami_id = _ami_from_machinesets()
    if not ami_id:
        print("  No machinesets found, using machine AMI configuration...")
        ami_id = _try_oc_jsonpath(
            [
                "get",
                "machines",
                "-n",
                "openshift-machine-api",
                "-o",
                "jsonpath={.items[0].spec.providerSpec.value.ami.id}",
            ]
        )

    if not cluster_name or not region or not az or not ami_id:
        raise RuntimeError(
            "Failed to retrieve cluster information. Is this an OpenShift cluster on AWS?"
        )

    return AwsClusterContext(
        cluster_name=cluster_name,
        region=region,
        availability_zone=az,
        infra_id=infra_id,
        ami_id=ami_id,
    )


def _ami_from_machinesets() -> str:
    try:
        p = run_cmd(
            ["oc", "get", "machineset", "-n", "openshift-machine-api", "-o", "json"],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return ""

    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return ""

    for item in data.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        if "gpu" in name:
            continue
        try:
            return item["spec"]["template"]["spec"]["providerSpec"]["value"]["ami"]["id"]
        except (KeyError, TypeError):
            continue
    return ""
