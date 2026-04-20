import click

from openshift_infra_deploy.efs_deploy import run_efs_deploy


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--storage-class-name",
    envvar="STORAGE_CLASS_NAME",
    default="efs-csi",
    show_default=True,
    help="StorageClass name passed to Helm as storageClass.name.",
)
@click.option(
    "--efs-file-system-id",
    envvar="EFS_FILE_SYSTEM_ID",
    default=None,
    help="If set, skip AWS EFS creation and use this fs-xxxxx id.",
)
def main(storage_class_name: str, efs_file_system_id: str | None) -> None:
    """Create or reuse EFS, then sync the efs-aws Argo CD app (CSI driver + StorageClass)."""
    fs = (efs_file_system_id or "").strip() or None
    run_efs_deploy(storage_class_name=storage_class_name, efs_file_system_id=fs)
