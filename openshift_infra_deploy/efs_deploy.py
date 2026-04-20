import random
import subprocess
import time
from pathlib import Path

from openshift_infra_deploy.argocd_util import (
    application_exists,
    argocd_admin_login_verbose,
    argocd_app_enable_autosync,
    argocd_app_set_params,
    argocd_app_sync,
    ensure_argocd_cli,
    oc_apply_gitops_manifest,
)
from openshift_infra_deploy.paths import openshift_infra_root
from openshift_infra_deploy.subprocess_util import run_cmd

_PROVISION_SCRIPT = r"""set -euo pipefail
: "${REGION:?}"
: "${CLUSTER_NAME:?}"
: "${AWS_ACCESS_KEY_ID:?}"
: "${AWS_SECRET_ACCESS_KEY:?}"
export AWS_DEFAULT_REGION="${REGION}"

echo "Discovering VPC..."
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:kubernetes.io/cluster/${CLUSTER_NAME},Values=owned" \
  --query 'Vpcs[0].VpcId' --output text)
if [ "${VPC_ID}" = "None" ] || [ -z "${VPC_ID}" ]; then
  echo "ERROR: No VPC found with tag kubernetes.io/cluster/${CLUSTER_NAME}=owned"
  exit 1
fi
echo "VPC_ID=${VPC_ID}"

echo "Discovering subnets..."
SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
            "Name=tag:kubernetes.io/cluster/${CLUSTER_NAME},Values=owned" \
  --query 'Subnets[*].SubnetId' --output text)
if [ -z "${SUBNET_IDS}" ]; then
  echo "ERROR: No subnets found for cluster ${CLUSTER_NAME}"
  exit 1
fi
echo "SUBNET_IDS=${SUBNET_IDS}"

echo "Discovering worker (node) security group..."
WORKER_SG=$(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
            "Name=tag:Name,Values=${CLUSTER_NAME}-node" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "${WORKER_SG}" = "None" ] || [ -z "${WORKER_SG}" ]; then
  echo "Retrying: SG tag Name=${CLUSTER_NAME}-node not found; trying legacy *worker* name pattern..."
  WORKER_SG=$(aws ec2 describe-security-groups \
    --filters "Name=vpc-id,Values=${VPC_ID}" \
              "Name=tag:kubernetes.io/cluster/${CLUSTER_NAME},Values=owned" \
    --query "SecurityGroups[?contains(GroupName, 'worker')].GroupId | [0]" --output text 2>/dev/null || true)
fi
if [ "${WORKER_SG}" = "None" ] || [ -z "${WORKER_SG}" ]; then
  echo "ERROR: Could not resolve worker security group. Expected tag Name=${CLUSTER_NAME}-node in VPC ${VPC_ID}."
  echo "  List candidates: aws ec2 describe-security-groups --filters \"Name=vpc-id,Values=${VPC_ID}\""
  exit 1
fi
echo "WORKER_SG=${WORKER_SG}"

echo "Creating EFS file system..."
FS_ID=$(aws efs create-file-system \
  --performance-mode generalPurpose \
  --throughput-mode bursting \
  --encrypted \
  --query 'FileSystemId' --output text)
echo "EFS FS_ID=${FS_ID}"

echo "Waiting for EFS filesystem to become available..."
while true; do
  FS_STATE=$(aws efs describe-file-systems --file-system-id "${FS_ID}" --query 'FileSystems[0].LifeCycleState' --output text)
  if [ "${FS_STATE}" = "available" ]; then break; fi
  echo "  ${FS_STATE}..."
  sleep 5
done

echo "Creating security group for EFS mount targets..."
EFS_SG=$(aws ec2 create-security-group \
  --group-name "${CLUSTER_NAME}-efs-$(echo "${FS_ID}" | tr '-' '_')" \
  --description "EFS NFS for ${CLUSTER_NAME}" \
  --vpc-id "${VPC_ID}" \
  --query 'GroupId' --output text)
aws ec2 create-tags --resources "${EFS_SG}" --tags "Key=Name,Value=${CLUSTER_NAME}-efs-nfs"

aws ec2 authorize-security-group-ingress \
  --group-id "${EFS_SG}" \
  --protocol tcp \
  --port 2049 \
  --source-group "${WORKER_SG}" \
  >/dev/null

for SUBNET in ${SUBNET_IDS}; do
  echo "Creating mount target in ${SUBNET}..."
  aws efs create-mount-target \
    --file-system-id "${FS_ID}" \
    --subnet-id "${SUBNET}" \
    --security-groups "${EFS_SG}" \
    >/dev/null
done

echo "Waiting for mount targets to become available..."
while true; do
  STATES=$(aws efs describe-mount-targets --file-system-id "${FS_ID}" --query 'MountTargets[].LifeCycleState' --output text || true)
  if echo "${STATES}" | grep -q 'creating'; then
    echo "  ${STATES}"
    sleep 10
    continue
  fi
  if echo "${STATES}" | grep -q error; then
    echo "ERROR: mount target lifecycle: ${STATES}"
    exit 1
  fi
  break
done

echo "EFS_FILE_SYSTEM_ID=${FS_ID}"
"""

APP_NAME = "efs-aws"
CREDS_NAMESPACE = "kube-system"
SECRET_AWS_CREDS = "aws-creds"
JOB_NAMESPACE = "kube-system"


def _cluster_name_region() -> tuple[str, str]:
    cluster = run_cmd(
        [
            "oc",
            "get",
            "infrastructure",
            "cluster",
            "-o",
            "jsonpath={.status.infrastructureName}",
        ],
        capture_output=True,
    ).stdout.strip()
    region = run_cmd(
        [
            "oc",
            "get",
            "infrastructure",
            "cluster",
            "-o",
            "jsonpath={.status.platformStatus.aws.region}",
        ],
        capture_output=True,
    ).stdout.strip()
    if not cluster or not region:
        raise SystemExit("Error: Could not read cluster name / AWS region from Infrastructure.")
    return cluster, region


def _provision_efs_via_job(cluster_name: str, region: str) -> str:
    job_name = f"efs-provision-{int(time.time())}-{random.randint(0, 99999)}"
    cm_name = f"{job_name}-script"

    run_cmd(
        ["oc", "delete", "job", job_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
        capture_output=True,
    )
    run_cmd(
        ["oc", "delete", "configmap", cm_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
        capture_output=True,
    )

    run_cmd(
        [
            "oc",
            "create",
            "configmap",
            cm_name,
            "-n",
            JOB_NAMESPACE,
            "--from-literal=provision.sh=" + _PROVISION_SCRIPT,
        ],
        capture_output=True,
    )

    job_yaml = f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {JOB_NAMESPACE}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: aws-cli
          image: amazon/aws-cli:latest
          env:
            - name: REGION
              value: "{region}"
            - name: CLUSTER_NAME
              value: "{cluster_name}"
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: {SECRET_AWS_CREDS}
                  key: aws_access_key_id
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: {SECRET_AWS_CREDS}
                  key: aws_secret_access_key
          command: ["/bin/bash", "/scripts/provision.sh"]
          volumeMounts:
            - name: scripts
              mountPath: /scripts
              readOnly: true
      volumes:
        - name: scripts
          configMap:
            name: {cm_name}
            defaultMode: 0755
"""
    subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=job_yaml,
        text=True,
        check=True,
    )

    w = subprocess.run(
        [
            "oc",
            "wait",
            "--for=condition=complete",
            f"job/{job_name}",
            "-n",
            JOB_NAMESPACE,
            "--timeout=600s",
        ],
        capture_output=True,
        text=True,
    )
    if w.returncode != 0:
        print("Job failed or timed out. Logs:")
        subprocess.run(["oc", "logs", f"job/{job_name}", "-n", JOB_NAMESPACE], check=False)
        run_cmd(
            ["oc", "delete", "job", job_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
            capture_output=True,
        )
        run_cmd(
            ["oc", "delete", "configmap", cm_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
            capture_output=True,
        )
        raise SystemExit(1)

    logs = run_cmd(["oc", "logs", f"job/{job_name}", "-n", JOB_NAMESPACE], capture_output=True).stdout
    print(logs)
    fs_id = ""
    for line in logs.splitlines():
        if line.startswith("EFS_FILE_SYSTEM_ID="):
            fs_id = line.split("=", 1)[1].strip()

    run_cmd(
        ["oc", "delete", "job", job_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
        capture_output=True,
    )
    run_cmd(
        ["oc", "delete", "configmap", cm_name, "-n", JOB_NAMESPACE, "--ignore-not-found"],
        capture_output=True,
    )

    if not fs_id:
        raise SystemExit("Error: Could not parse EFS_FILE_SYSTEM_ID from job logs.")
    return fs_id


def run_efs_deploy(*, storage_class_name: str, efs_file_system_id: str | None) -> None:
    root = openshift_infra_root()
    gitops_file = root / "gitops" / "infra" / "efs-aws.yaml"

    print("===================================")
    print("AWS EFS RWX (EFS CSI + StorageClass)")
    print("===================================")
    print(f"StorageClass name: {storage_class_name}")
    print()

    print("Step 1: Gathering cluster information...")
    cluster_name, region = _cluster_name_region()
    print(f"  Cluster: {cluster_name}")
    print(f"  Region:  {region}")
    print()

    fs_id = (efs_file_system_id or "").strip() or None
    if not fs_id:
        print(f"Step 2: Provisioning EFS via Job in {JOB_NAMESPACE} (secret {SECRET_AWS_CREDS})...")
        fs_id = _provision_efs_via_job(cluster_name, region)
        print(f"  EFS FileSystemId: {fs_id}")
        print()
    else:
        print(f"Step 2: Skipping EFS creation (using EFS_FILE_SYSTEM_ID={fs_id}).")
        print()

    print("Step 3: Logging in to ArgoCD...")
    ensure_argocd_cli()
    try:
        server = argocd_admin_login_verbose()
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    print(f"  Logged in to ArgoCD at {server}")
    print()

    print("Step 4: Creating ArgoCD Application...")
    if application_exists(APP_NAME):
        print(f"  Application '{APP_NAME}' already exists.")
    else:
        oc_apply_gitops_manifest(gitops_file)
        print(f"  Created application '{APP_NAME}'")
    print()

    print("Step 5: Setting Helm parameters and syncing...")
    try:
        run_cmd(
            [
                "argocd",
                "app",
                "set",
                APP_NAME,
                "-p",
                f"fileSystemId={fs_id}",
                "-p",
                f"region={region}",
                "-p",
                f"clusterName={cluster_name}",
                "-p",
                f"storageClass.name={storage_class_name}",
            ],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        print(
            f"Error: argocd app set failed (is application '{APP_NAME}' missing? Check step 4 output above.)"
        )
        raise SystemExit(1) from None

    try:
        subprocess.run(["argocd", "app", "sync", APP_NAME], check=True)
    except subprocess.CalledProcessError:
        print(f"Error: argocd app sync failed. Try: argocd app get {APP_NAME}")
        raise SystemExit(1) from None
    print("  Application synced")

    try:
        argocd_app_enable_autosync(APP_NAME)
    except subprocess.CalledProcessError:
        pass
    print()

    print("===================================")
    print("Done.")
    print("===================================")
    print(f"StorageClass: {storage_class_name}  (RWX via EFS)")
    print(f"EFS fileSystemId: {fs_id}")
    print()
    print("Verify:")
    print(f"  oc get storageclass {storage_class_name}")
    print("  oc get clusterscsidriver efs.csi.aws.com")
    print(
        "  oc get pods -n openshift-cluster-csi-drivers -l app.kubernetes.io/name=aws-efs-csi-driver"
    )
    print()
