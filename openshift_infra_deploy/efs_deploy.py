import configparser
import getpass
import json
import os
import random
import re
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
SECRET_NAME = "aws-creds"
SECRET_NAMESPACE = "kube-system"
JOB_NAMESPACE = "kube-system"

EFS_CSI_CREDS_NAMESPACE = "openshift-cluster-csi-drivers"
EFS_CSI_CONTROLLER_SECRET = "aws-efs-cloud-credentials"
EFS_CSI_NODE_SECRET = "node-aws-efs-cloud-credentials"


# ---------------------------------------------------------------------------
# AWS credential helpers
# ---------------------------------------------------------------------------

def _secret_has_aws_keys(name: str, namespace: str) -> bool:
    r = run_cmd(
        ["oc", "get", "secret", name, "-n", namespace, "-o", "jsonpath={.data}"],
        check=False, capture_output=True,
    )
    if r.returncode != 0:
        return False
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    return "aws_access_key_id" in data and "aws_secret_access_key" in data


def _get_cluster_aws_account() -> str | None:
    """Discover the cluster's AWS account ID from a CCO-minted secret annotation."""
    r = run_cmd(
        ["oc", "get", "secrets", "-A",
         "-l", "cloudcredential.openshift.io/credentials-request=true",
         "-o", "jsonpath={.items[0].metadata.annotations.cloudcredential\\.openshift\\.io/aws-policy-last-applied}"],
        check=False, capture_output=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        m = re.search(r"arn:aws:iam::(\d+):", r.stdout)
        if m:
            return m.group(1)
    return None


def _validate_aws_creds(
    access_key: str, secret_key: str, region: str,
) -> tuple[str | None, str | None]:
    """Validate credentials via sts:GetCallerIdentity.

    Returns (account_id, arn) on success, (None, None) on failure.
    """
    r = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--output", "json"],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "AWS_ACCESS_KEY_ID": access_key,
             "AWS_SECRET_ACCESS_KEY": secret_key,
             "AWS_DEFAULT_REGION": region},
    )
    if r.returncode != 0:
        return None, None
    try:
        data = json.loads(r.stdout)
        return data.get("Account"), data.get("Arn")
    except (json.JSONDecodeError, ValueError):
        return None, None


def _read_aws_credentials_file() -> tuple[str, str] | None:
    """Read access key / secret from ~/.aws/credentials (default or AWS_PROFILE)."""
    creds_file = Path.home() / ".aws" / "credentials"
    if not creds_file.exists():
        return None
    profile = os.environ.get("AWS_PROFILE", "default")
    cfg = configparser.ConfigParser()
    cfg.read(creds_file)
    if profile not in cfg:
        return None
    ak = cfg[profile].get("aws_access_key_id", "").strip()
    sk = cfg[profile].get("aws_secret_access_key", "").strip()
    if ak and sk:
        return ak, sk
    return None


def _create_aws_secret(name: str, namespace: str, ak: str, sk: str) -> None:
    """Create (or replace) an opaque secret with AWS keys."""
    run_cmd(
        ["oc", "delete", "secret", name, "-n", namespace, "--ignore-not-found"],
        capture_output=True,
    )
    run_cmd(
        ["oc", "create", "secret", "generic", name, "-n", namespace,
         f"--from-literal=aws_access_key_id={ak}",
         f"--from-literal=aws_secret_access_key={sk}"],
        capture_output=True,
    )


def _delete_secret(name: str, namespace: str) -> None:
    run_cmd(
        ["oc", "delete", "secret", name, "-n", namespace, "--ignore-not-found"],
        capture_output=True,
    )


def _prompt_and_validate_credentials(
    region: str, cluster_account: str | None,
) -> tuple[str, str]:
    """Interactively prompt for AWS credentials and validate them.

    If the user presses Enter without typing anything, falls back to
    ~/.aws/credentials.  Validates via sts:GetCallerIdentity and checks
    the AWS account matches the cluster.

    Returns (access_key, secret_key) on success; raises SystemExit on failure.
    """
    print()
    print("  The cluster does not have a pre-existing AWS credential secret.")
    if cluster_account:
        print(f"  Cluster AWS account: {cluster_account}")
    print()
    print("  Enter AWS credentials for the account that owns this cluster's VPC.")
    print("  (Press Enter to use ~/.aws/credentials instead.)")
    print()

    ak = input("  AWS Access Key ID     : ").strip()
    sk = ""
    source = ""
    if ak:
        sk = getpass.getpass("  AWS Secret Access Key : ").strip()
        if not sk:
            raise SystemExit("Error: Secret access key cannot be empty when access key is provided.")
        source = "user input"
    else:
        # Fallback to aws credentials file
        profile = os.environ.get("AWS_PROFILE", "default")
        local = _read_aws_credentials_file()
        if not local:
            raise SystemExit(
                f"Error: No input provided and ~/.aws/credentials "
                f"(profile: {profile}) not found or incomplete.\n"
                "  Run 'aws configure' first, or enter credentials manually."
            )
        ak, sk = local
        source = f"~/.aws/credentials (profile: {profile})"

    print()
    print(f"  Credential source: {source}")
    print(f"  Access Key ID:     {ak[:4]}{'*' * (len(ak) - 8)}{ak[-4:]}")
    print("  Validating credentials...", end=" ", flush=True)

    try:
        account_id, arn = _validate_aws_creds(ak, sk, region)
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        raise SystemExit(
            "Error: AWS STS call timed out. Check network connectivity and credentials."
        ) from None

    if account_id is None:
        print("FAILED")
        raise SystemExit(
            "Error: AWS credentials are invalid (sts:GetCallerIdentity failed).\n"
            "  Verify your access key and secret are correct and not expired."
        )

    print("OK")
    print(f"  AWS Account:       {account_id}")
    print(f"  IAM Identity:      {arn}")

    if cluster_account and account_id != cluster_account:
        print()
        raise SystemExit(
            f"Error: AWS account mismatch.\n"
            f"  Credentials are for account {account_id}\n"
            f"  Cluster is in account       {cluster_account}\n"
            f"  You need credentials for the account that owns the cluster's VPC."
        )

    return ak, sk


# ---------------------------------------------------------------------------
# EFS CSI driver credential secrets
# ---------------------------------------------------------------------------

def _ensure_efs_csi_credentials(ak: str, sk: str) -> None:
    """Ensure the EFS CSI driver has its required AWS credential secrets.

    On clusters where the CCO has root credentials these are minted
    automatically via CredentialsRequests.  On clusters without root
    creds the secrets must be created directly.
    """
    creds_ini = f"[default]\naws_access_key_id = {ak}\naws_secret_access_key = {sk}\n"
    for secret_name in (EFS_CSI_CONTROLLER_SECRET, EFS_CSI_NODE_SECRET):
        if _secret_has_aws_keys(secret_name, EFS_CSI_CREDS_NAMESPACE):
            print(f"  {EFS_CSI_CREDS_NAMESPACE}/{secret_name} already exists.")
            continue
        run_cmd(
            ["oc", "delete", "secret", secret_name, "-n", EFS_CSI_CREDS_NAMESPACE,
             "--ignore-not-found"],
            capture_output=True,
        )
        run_cmd(
            ["oc", "create", "secret", "generic", secret_name,
             "-n", EFS_CSI_CREDS_NAMESPACE,
             f"--from-literal=aws_access_key_id={ak}",
             f"--from-literal=aws_secret_access_key={sk}",
             f"--from-literal=credentials={creds_ini}"],
            capture_output=True,
        )
        print(f"  Created {EFS_CSI_CREDS_NAMESPACE}/{secret_name}.")


# ---------------------------------------------------------------------------
# Cluster info
# ---------------------------------------------------------------------------

def _cluster_name_region() -> tuple[str, str]:
    cluster = run_cmd(
        ["oc", "get", "infrastructure", "cluster",
         "-o", "jsonpath={.status.infrastructureName}"],
        capture_output=True,
    ).stdout.strip()
    region = run_cmd(
        ["oc", "get", "infrastructure", "cluster",
         "-o", "jsonpath={.status.platformStatus.aws.region}"],
        capture_output=True,
    ).stdout.strip()
    if not cluster or not region:
        raise SystemExit("Error: Could not read cluster name / AWS region from Infrastructure.")
    return cluster, region


# ---------------------------------------------------------------------------
# EFS provisioning Job
# ---------------------------------------------------------------------------

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
        ["oc", "create", "configmap", cm_name, "-n", JOB_NAMESPACE,
         "--from-literal=provision.sh=" + _PROVISION_SCRIPT],
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
                  name: {SECRET_NAME}
                  key: aws_access_key_id
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: {SECRET_NAME}
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
        input=job_yaml, text=True, check=True,
    )

    w = subprocess.run(
        ["oc", "wait", "--for=condition=complete",
         f"job/{job_name}", "-n", JOB_NAMESPACE, "--timeout=600s"],
        capture_output=True, text=True,
    )
    if w.returncode != 0:
        print("  Job failed or timed out. Logs:")
        subprocess.run(
            ["oc", "logs", f"job/{job_name}", "-n", JOB_NAMESPACE], check=False,
        )
        run_cmd(["oc", "delete", "job", job_name, "-n", JOB_NAMESPACE,
                 "--ignore-not-found"], capture_output=True)
        run_cmd(["oc", "delete", "configmap", cm_name, "-n", JOB_NAMESPACE,
                 "--ignore-not-found"], capture_output=True)
        raise SystemExit(1)

    logs = run_cmd(
        ["oc", "logs", f"job/{job_name}", "-n", JOB_NAMESPACE],
        capture_output=True,
    ).stdout
    print(logs)

    fs_id = ""
    for line in logs.splitlines():
        if line.startswith("EFS_FILE_SYSTEM_ID="):
            fs_id = line.split("=", 1)[1].strip()

    run_cmd(["oc", "delete", "job", job_name, "-n", JOB_NAMESPACE,
             "--ignore-not-found"], capture_output=True)
    run_cmd(["oc", "delete", "configmap", cm_name, "-n", JOB_NAMESPACE,
             "--ignore-not-found"], capture_output=True)

    if not fs_id:
        raise SystemExit("Error: Could not parse EFS_FILE_SYSTEM_ID from job logs.")
    return fs_id


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_efs_deploy(*, storage_class_name: str, efs_file_system_id: str | None) -> None:
    root = openshift_infra_root()
    gitops_file = root / "gitops" / "infra" / "efs-aws.yaml"

    print("===================================")
    print("AWS EFS RWX (EFS CSI + StorageClass)")
    print("===================================")
    print(f"StorageClass name: {storage_class_name}")
    print()

    # -- Step 1: cluster info ------------------------------------------------
    print("Step 1: Gathering cluster information...")
    cluster_name, region = _cluster_name_region()
    print(f"  Cluster: {cluster_name}")
    print(f"  Region:  {region}")
    print()

    # -- Step 2: credentials -------------------------------------------------
    # Credentials used for both EFS provisioning and CSI driver secrets.
    aws_ak: str | None = None
    aws_sk: str | None = None
    secret_was_preexisting = _secret_has_aws_keys(SECRET_NAME, SECRET_NAMESPACE)

    print("Step 2: Resolving AWS credentials...")
    if secret_was_preexisting:
        print(f"  Found existing secret '{SECRET_NAMESPACE}/{SECRET_NAME}'.")
        print("  (Using cluster-provided credentials.)")
    else:
        cluster_account = _get_cluster_aws_account()
        aws_ak, aws_sk = _prompt_and_validate_credentials(region, cluster_account)
        _create_aws_secret(SECRET_NAME, SECRET_NAMESPACE, aws_ak, aws_sk)
        print(f"  Created secret '{SECRET_NAMESPACE}/{SECRET_NAME}'.")
    print()

    # -- Step 3: provision EFS -----------------------------------------------
    fs_id = (efs_file_system_id or "").strip() or None
    if not fs_id:
        print(f"Step 3: Provisioning EFS via Job in {JOB_NAMESPACE}...")
        try:
            fs_id = _provision_efs_via_job(cluster_name, region)
        finally:
            if not secret_was_preexisting:
                print(f"  Cleaning up temporary secret '{SECRET_NAMESPACE}/{SECRET_NAME}'...")
                _delete_secret(SECRET_NAME, SECRET_NAMESPACE)
        print(f"  EFS FileSystemId: {fs_id}")
        print()
    else:
        print(f"Step 3: Skipping EFS creation (using EFS_FILE_SYSTEM_ID={fs_id}).")
        if not secret_was_preexisting:
            _delete_secret(SECRET_NAME, SECRET_NAMESPACE)
        print()

    # -- Step 4: ArgoCD login ------------------------------------------------
    print("Step 4: Logging in to ArgoCD...")
    ensure_argocd_cli()
    try:
        server = argocd_admin_login_verbose()
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    print(f"  Logged in to ArgoCD at {server}")
    print()

    # -- Step 5: ArgoCD Application ------------------------------------------
    print("Step 5: Creating ArgoCD Application...")
    if application_exists(APP_NAME):
        print(f"  Application '{APP_NAME}' already exists.")
    else:
        oc_apply_gitops_manifest(gitops_file)
        print(f"  Created application '{APP_NAME}'")
    print()

    # -- Step 6: Helm params + sync ------------------------------------------
    print("Step 6: Setting Helm parameters and syncing...")
    try:
        run_cmd(
            ["argocd", "app", "set", APP_NAME,
             "-p", f"fileSystemId={fs_id}",
             "-p", f"region={region}",
             "-p", f"clusterName={cluster_name}",
             "-p", f"storageClass.name={storage_class_name}"],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        print(f"Error: argocd app set failed (is application '{APP_NAME}' missing?)")
        raise SystemExit(1) from None

    try:
        subprocess.run(["argocd", "app", "sync", APP_NAME], check=True)
    except subprocess.CalledProcessError:
        print(f"Error: argocd app sync failed. Try: argocd app get {APP_NAME}")
        raise SystemExit(1) from None
    print("  Application synced")
    print()

    # -- Step 7: EFS CSI driver credential secrets ---------------------------
    print("Step 7: Ensuring EFS CSI driver has AWS credentials...")
    if aws_ak and aws_sk:
        _ensure_efs_csi_credentials(aws_ak, aws_sk)
    else:
        # Credentials came from the pre-existing cluster secret; CSI secrets
        # are either already present or will be minted by the CCO.
        controller_ok = _secret_has_aws_keys(EFS_CSI_CONTROLLER_SECRET, EFS_CSI_CREDS_NAMESPACE)
        node_ok = _secret_has_aws_keys(EFS_CSI_NODE_SECRET, EFS_CSI_CREDS_NAMESPACE)
        if controller_ok and node_ok:
            print("  EFS CSI credential secrets already exist.")
        else:
            print("  Waiting for CCO to mint EFS CSI credential secrets...")
            deadline = time.time() + 120
            while time.time() < deadline:
                controller_ok = _secret_has_aws_keys(
                    EFS_CSI_CONTROLLER_SECRET, EFS_CSI_CREDS_NAMESPACE)
                node_ok = _secret_has_aws_keys(
                    EFS_CSI_NODE_SECRET, EFS_CSI_CREDS_NAMESPACE)
                if controller_ok and node_ok:
                    break
                time.sleep(5)
            if controller_ok and node_ok:
                print("  EFS CSI credential secrets provisioned by CCO.")
            else:
                print("  WARNING: EFS CSI credential secrets not yet provisioned.")
                print("  The CSI driver controller may fail to start.")
                print("  Check: oc get credentialsrequest -n openshift-cloud-credential-operator | grep efs")

    try:
        argocd_app_enable_autosync(APP_NAME)
    except subprocess.CalledProcessError:
        pass
    print()

    # -- Done ----------------------------------------------------------------
    print("===================================")
    print("Done.")
    print("===================================")
    print(f"StorageClass: {storage_class_name}  (RWX via EFS)")
    print(f"EFS fileSystemId: {fs_id}")
    print()
    print("Verify:")
    print(f"  oc get storageclass {storage_class_name}")
    print("  oc get clusterscsidriver efs.csi.aws.com")
    print("  oc get pods -n openshift-cluster-csi-drivers"
          " -l app.kubernetes.io/name=aws-efs-csi-driver")
    print()
