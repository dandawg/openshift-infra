#!/bin/bash
set -euo pipefail

# AWS EFS RWX storage: creates an EFS filesystem (unless EFS_FILE_SYSTEM_ID is set), then syncs GitOps
# (EFS CSI operator subscription, ClusterCSIDriver, StorageClass).
#
# Prerequisites: oc, argocd CLI, cluster-admin, OpenShift on AWS, OpenShift GitOps installed.
#
# Optional environment variables:
#   EFS_FILE_SYSTEM_ID   If set, skip AWS EFS creation and use this fs-xxxxx id.
#   STORAGE_CLASS_NAME   StorageClass name (default: efs-csi). Passed to Helm as storageClass.name.

STORAGE_CLASS_NAME="${STORAGE_CLASS_NAME:-efs-csi}"
APP_NAME="efs-aws"
# kube-system/aws-creds (the sandbox "student" admin) has elasticfilesystem:* permissions.
# openshift-machine-api/aws-cloud-credentials only covers EC2/machine operations — not EFS.
CREDS_NAMESPACE="kube-system"
SECRET_AWS_CREDS="aws-creds"
JOB_NAMESPACE="kube-system"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
GITOPS_FILE="${REPO_ROOT}/gitops/infra/efs-aws.yaml"

echo "==================================="
echo "AWS EFS RWX (EFS CSI + StorageClass)"
echo "==================================="
echo "StorageClass name: ${STORAGE_CLASS_NAME}"
echo ""

echo "Step 1: Gathering cluster information..."
CLUSTER_NAME="$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')"
REGION="$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')"

if [ -z "${CLUSTER_NAME}" ] || [ -z "${REGION}" ]; then
  echo "Error: Could not read cluster name / AWS region from Infrastructure."
  exit 1
fi

echo "  Cluster: ${CLUSTER_NAME}"
echo "  Region:  ${REGION}"
echo ""

FS_ID="${EFS_FILE_SYSTEM_ID:-}"

if [ -z "${FS_ID}" ]; then
  echo "Step 2: Provisioning EFS via Job in ${JOB_NAMESPACE} (secret ${SECRET_AWS_CREDS})..."
  JOB_NAME="efs-provision-$(date +%s)-${RANDOM}"
  CM_NAME="${JOB_NAME}-script"

  oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
  oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true

  PROVISION_SCRIPT="$(cat <<'EOSCRIPT'
set -euo pipefail
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
# OpenShift IPI/AWS tags the compute SG as Name=<infrastructureName>-node (same value as CLUSTER_NAME here).
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
EOSCRIPT
)"

  oc create configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --from-literal=provision.sh="${PROVISION_SCRIPT}" >/dev/null

  cat <<JOBYAML | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: ${JOB_NAMESPACE}
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
              value: "${REGION}"
            - name: CLUSTER_NAME
              value: "${CLUSTER_NAME}"
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: ${SECRET_AWS_CREDS}
                  key: aws_access_key_id
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: ${SECRET_AWS_CREDS}
                  key: aws_secret_access_key
          command: ["/bin/bash", "/scripts/provision.sh"]
          volumeMounts:
            - name: scripts
              mountPath: /scripts
              readOnly: true
      volumes:
        - name: scripts
          configMap:
            name: ${CM_NAME}
            defaultMode: 0755
JOBYAML

  if ! oc wait --for=condition=complete "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" --timeout=600s; then
    echo "Job failed or timed out. Logs:"
    oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" || true
    oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
    oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
    exit 1
  fi

  LOGS="$(oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}")"
  echo "${LOGS}"
  FS_ID="$(echo "${LOGS}" | grep '^EFS_FILE_SYSTEM_ID=' | tail -1 | cut -d= -f2-)"
  oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
  oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null

  if [ -z "${FS_ID}" ]; then
    echo "Error: Could not parse EFS_FILE_SYSTEM_ID from job logs."
    exit 1
  fi
  echo "  EFS FileSystemId: ${FS_ID}"
  echo ""
else
  echo "Step 2: Skipping EFS creation (using EFS_FILE_SYSTEM_ID=${FS_ID})."
  echo ""
fi

echo "Step 3: Logging in to ArgoCD..."
ARGOCD_PASSWORD="$(oc get secret/openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' | base64 -d)"
ARGOCD_SERVER="$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')"

if [ -z "${ARGOCD_SERVER}" ]; then
  echo "Error: OpenShift GitOps not found. Run ./bootstrap.sh (repo root) first."
  exit 1
fi

argocd login "${ARGOCD_SERVER}" --username admin --password "${ARGOCD_PASSWORD}" --insecure >/dev/null 2>&1
echo "  Logged in to ArgoCD at ${ARGOCD_SERVER}"
echo ""

echo "Step 4: Creating ArgoCD Application..."
if oc get application "${APP_NAME}" -n openshift-gitops >/dev/null 2>&1; then
  echo "  Application '${APP_NAME}' already exists."
else
  oc apply -f "${GITOPS_FILE}"
  echo "  Created application '${APP_NAME}'"
fi
echo ""

echo "Step 5: Setting Helm parameters and syncing..."
argocd app set "${APP_NAME}" \
  -p "fileSystemId=${FS_ID}" \
  -p "region=${REGION}" \
  -p "clusterName=${CLUSTER_NAME}" \
  -p "storageClass.name=${STORAGE_CLASS_NAME}" >/dev/null

argocd app set "${APP_NAME}" --sync-policy automated --auto-prune --self-heal >/dev/null 2>&1 || true
argocd app sync "${APP_NAME}"
echo "  Application synced"
echo ""

echo "==================================="
echo "Done."
echo "==================================="
echo "StorageClass: ${STORAGE_CLASS_NAME}  (RWX via EFS)"
echo "EFS fileSystemId: ${FS_ID}"
echo ""
echo "Verify:"
echo "  oc get storageclass ${STORAGE_CLASS_NAME}"
echo "  oc get clusterscsidriver efs.csi.aws.com"
echo "  oc get pods -n openshift-cluster-csi-drivers -l app.kubernetes.io/name=aws-efs-csi-driver"
echo ""
