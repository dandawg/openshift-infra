from dataclasses import dataclass
from typing import Literal

from openshift_infra_deploy.argocd_util import (
    application_exists,
    argocd_admin_login,
    argocd_app_enable_autosync,
    argocd_app_set_params,
    argocd_app_sync,
    oc_apply_gitops_manifest,
)
from openshift_infra_deploy.cluster import gather_aws_cluster_context
from openshift_infra_deploy.paths import openshift_infra_root


@dataclass(frozen=True)
class MachineSetProfile:
    gitops_relative: str
    app_name: str
    machine_name_suffix: str
    default_root_volume_gb: int


GPU_PROFILES: dict[str, MachineSetProfile] = {
    "g4dn.xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-g4dn-xlarge.yaml",
        "gpu-machineset-aws-g4dn-xlarge",
        "g4dn",
        120,
    ),
    "g6.2xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-g6.yaml",
        "gpu-machineset-aws-g6",
        "g6",
        120,
    ),
    "g6.4xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-g6-4xlarge.yaml",
        "gpu-machineset-aws-g6-4xlarge",
        "g6-4x",
        120,
    ),
    "g6e.2xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-g6e.yaml",
        "gpu-machineset-aws-g6e",
        "g6e",
        120,
    ),
    "g6e.12xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-g6e-12xlarge.yaml",
        "gpu-machineset-aws-g6e-12xlarge",
        "g6e-12xl",
        120,
    ),
    "p5.4xlarge": MachineSetProfile(
        "gitops/infra/gpu-machineset-aws-p5-4xlarge.yaml",
        "gpu-machineset-aws-p5-4xlarge",
        "p5-4x",
        200,
    ),
}

CPU_PROFILES: dict[str, MachineSetProfile] = {
    "m6a.4xlarge": MachineSetProfile(
        "gitops/infra/cpu-machineset-aws-m6a-4xlarge.yaml",
        "cpu-machineset-aws-m6a-4xlarge",
        "m6a-4x",
        120,
    ),
}

MachineSetKind = Literal["gpu", "cpu"]

_overlap = set(GPU_PROFILES) & set(CPU_PROFILES)
if _overlap:
    raise RuntimeError(f"GPU and CPU profiles share instance types: {_overlap}")


def resolve_machineset_profile(instance_type: str) -> tuple[MachineSetKind, MachineSetProfile]:
    if instance_type in GPU_PROFILES:
        return "gpu", GPU_PROFILES[instance_type]
    if instance_type in CPU_PROFILES:
        return "cpu", CPU_PROFILES[instance_type]
    raise KeyError(instance_type)


def machineset_list_instance_type_lines() -> list[str]:
    """Lines like 'gpu   g6.2xlarge' (kind then instance type), GPU block then CPU block."""
    lines = [f"gpu   {t}" for t in sorted(GPU_PROFILES)]
    lines.extend(f"cpu   {t}" for t in sorted(CPU_PROFILES))
    return lines


def machineset_unified_help_epilog() -> str:
    gpu_csv = ", ".join(sorted(GPU_PROFILES))
    cpu_csv = ", ".join(sorted(CPU_PROFILES))
    return (
        "Supported --instance-type values (GPU vs CPU is inferred from the type):\n\n"
        f"  gpu: {gpu_csv}\n\n"
        f"  cpu: {cpu_csv}\n\n"
        "Use --list-instance-types for one line per type with a gpu/cpu prefix."
    )


def wait_hint_lines_for_kind(kind: MachineSetKind) -> list[str]:
    if kind == "gpu":
        return [
            "Wait for GPU node (5-10 minutes):",
            "  oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s",
            "",
            "Verify GPU node:",
            "  oc get nodes -l nvidia.com/gpu.present=true",
        ]
    return [
        "Wait for CPU node (5-10 minutes):",
        "  oc wait --for=condition=Ready nodes -l node-role.kubernetes.io/worker --timeout=600s",
        "",
        "Verify CPU worker nodes:",
        "  oc get nodes -l node-role.kubernetes.io/worker",
    ]


def deploy_title_for_kind(kind: MachineSetKind) -> str:
    return "GPU MachineSet Deployment" if kind == "gpu" else "CPU MachineSet Deployment"


def resolve_replica_count(replicas: int | None, replica_count: int | None) -> int:
    """Replica count: Click supplies CLI > env (REPLICAS, REPLICA_COUNT) > default None here → 1."""
    if replicas is not None:
        return replicas
    if replica_count is not None:
        return replica_count
    return 1


def deploy_aws_machineset(
    *,
    title: str,
    instance_type: str,
    profile: MachineSetProfile,
    root_volume_size: int,
    root_volume_type: str,
    root_volume_iops: int,
    replicas: int,
    wait_hint_lines: list[str],
    bootstrap_hint: str | None = None,
) -> None:
    root = openshift_infra_root()
    gitops_path = root / profile.gitops_relative

    print("===================================")
    print(title)
    print("===================================")
    print(f"Instance Type: {instance_type}")
    print(f"Root Volume: {root_volume_size}GB {root_volume_type} ({root_volume_iops} IOPS)")
    print(f"Replicas: {replicas}")
    print()

    print("Step 1: Gathering cluster information...")
    try:
        ctx = gather_aws_cluster_context()
    except RuntimeError as e:
        raise SystemExit(f"Error: {e}") from e

    print(f"  Cluster Name: {ctx.cluster_name}")
    print(f"  Region: {ctx.region}")
    print(f"  Availability Zone: {ctx.availability_zone}")
    print(f"  Infrastructure ID: {ctx.infra_id}")
    print(f"  AMI ID: {ctx.ami_id}")
    print()

    print("Step 2: Logging in to ArgoCD...")
    try:
        server = argocd_admin_login()
    except RuntimeError as e:
        msg = str(e)
        if bootstrap_hint and "GitOps not found" in msg:
            msg = f"{msg}\n{bootstrap_hint}"
        raise SystemExit(f"Error: {msg}") from e
    print(f"  Logged in to ArgoCD at {server}")
    print()

    print("Step 3: Creating ArgoCD Application...")
    if application_exists(profile.app_name):
        print(f"  Application '{profile.app_name}' already exists. Skipping creation.")
    else:
        oc_apply_gitops_manifest(gitops_path)
        print(f"  Created application '{profile.app_name}'")
    print()

    print("Step 4: Setting Helm parameters...")
    argocd_app_set_params(
        profile.app_name,
        [
            ("clusterName", ctx.cluster_name),
            ("region", ctx.region),
            ("availabilityZone", ctx.availability_zone),
            ("infraID", ctx.infra_id),
            ("amiId", ctx.ami_id),
            ("rootVolume.size", str(root_volume_size)),
            ("rootVolume.type", root_volume_type),
            ("rootVolume.iops", str(root_volume_iops)),
            ("instanceType", instance_type),
            ("machineNameSuffix", profile.machine_name_suffix),
            ("replicas", str(replicas)),
        ],
    )
    print("  Parameters configured")
    print()

    print("Step 5: Syncing application...")
    argocd_app_enable_autosync(profile.app_name)
    argocd_app_sync(profile.app_name)
    print("  Application synced")
    print()

    print("===================================")
    print("Deployment initiated successfully!")
    print("===================================")
    print()
    print("Monitor progress with:")
    print("  oc get machineset -n openshift-machine-api -w")
    print("  oc get machine -n openshift-machine-api")
    print()
    for line in wait_hint_lines:
        print(line)
    print()
