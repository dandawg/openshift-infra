import click

from openshift_infra_deploy.machineset_deploy import (
    deploy_aws_machineset,
    deploy_title_for_kind,
    machineset_list_instance_type_lines,
    machineset_unified_help_epilog,
    resolve_machineset_profile,
    resolve_replica_count,
    wait_hint_lines_for_kind,
)


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=machineset_unified_help_epilog(),
)
@click.option(
    "--list-instance-types",
    is_flag=True,
    help="Print all supported instance types with gpu/cpu prefix, then exit.",
)
@click.option(
    "--instance-type",
    envvar="INSTANCE_TYPE",
    default="g6.2xlarge",
    show_default=True,
    help="EC2 instance type; GPU vs CPU is inferred (see epilog).",
)
@click.option(
    "--root-volume-size",
    envvar="ROOT_VOLUME_SIZE",
    type=int,
    default=None,
    help="Root volume size (GiB). When omitted, uses the profile default (e.g. 200 for p5.4xlarge).",
)
@click.option(
    "--root-volume-type",
    envvar="ROOT_VOLUME_TYPE",
    default="gp3",
    show_default=True,
)
@click.option(
    "--root-volume-iops",
    envvar="ROOT_VOLUME_IOPS",
    type=int,
    default=3000,
    show_default=True,
)
@click.option(
    "--replicas",
    "replicas_opt",
    envvar="REPLICAS",
    type=int,
    default=None,
    help="Helm replicas (env REPLICAS). Precedence: CLI > env > --replica-count.",
)
@click.option(
    "--replica-count",
    envvar="REPLICA_COUNT",
    type=int,
    default=None,
    help="Replica count (env REPLICA_COUNT) when --replicas is not set.",
)
def main(
    list_instance_types: bool,
    instance_type: str,
    root_volume_size: int | None,
    root_volume_type: str,
    root_volume_iops: int,
    replicas_opt: int | None,
    replica_count: int | None,
) -> None:
    """Deploy an AWS GPU or CPU MachineSet via OpenShift GitOps (Argo CD) and Helm."""
    if list_instance_types:
        click.echo("\n".join(machineset_list_instance_type_lines()))
        return

    try:
        kind, profile = resolve_machineset_profile(instance_type)
    except KeyError:
        raise SystemExit(
            f"Error: Unsupported instance type {instance_type!r}.\n"
            "Run with --list-instance-types to see supported values."
        ) from None

    vol = root_volume_size if root_volume_size is not None else profile.default_root_volume_gb
    replicas = resolve_replica_count(replicas_opt, replica_count)
    bootstrap = "Run: ./bootstrap.sh" if kind == "cpu" else None

    deploy_aws_machineset(
        title=deploy_title_for_kind(kind),
        instance_type=instance_type,
        profile=profile,
        root_volume_size=vol,
        root_volume_type=root_volume_type,
        root_volume_iops=root_volume_iops,
        replicas=replicas,
        wait_hint_lines=wait_hint_lines_for_kind(kind),
        bootstrap_hint=bootstrap,
    )
