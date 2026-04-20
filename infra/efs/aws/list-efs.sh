#!/bin/bash
set -euo pipefail

# List EFS file system IDs using the same in-cluster credentials as deploy.py
# (kube-system/aws-creds). No AWS console or local aws CLI required.
#
# Environment:
#   LIST_EFS_SCOPE   cluster (default) — EFS whose mount targets sit in subnets
#                    belonging to this cluster's tagged VPC
#                    region — every EFS in the account/region (wider view)

CREDS_NAMESPACE="kube-system"
SECRET_AWS_CREDS="aws-creds"
JOB_NAMESPACE="kube-system"
LIST_EFS_SCOPE="${LIST_EFS_SCOPE:-cluster}"

if ! oc get secret "${SECRET_AWS_CREDS}" -n "${CREDS_NAMESPACE}" >/dev/null 2>&1; then
  echo "Error: secret ${CREDS_NAMESPACE}/${SECRET_AWS_CREDS} not found."
  echo "  This script needs the same credential secret as ./infra/efs/aws/deploy.py"
  exit 1
fi

echo "==================================="
echo "List EFS (scope: ${LIST_EFS_SCOPE})"
echo "==================================="
echo ""

CLUSTER_NAME="$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')"
REGION="$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')"

if [ -z "${CLUSTER_NAME}" ] || [ -z "${REGION}" ]; then
  echo "Error: Could not read cluster name / AWS region from Infrastructure."
  exit 1
fi

echo "  Cluster: ${CLUSTER_NAME}"
echo "  Region:  ${REGION}"
echo ""

JOB_NAME="efs-list-$(date +%s)-${RANDOM}"
CM_NAME="${JOB_NAME}-script"

oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true

LIST_SCRIPT="$(cat <<'EOSCRIPT'
set -euo pipefail
: "${REGION:?}"
: "${LIST_EFS_SCOPE:?}"
export AWS_DEFAULT_REGION="${REGION}"

if [ "${LIST_EFS_SCOPE}" = "region" ]; then
  echo "All EFS file systems in region ${REGION}:"
  echo ""
  aws efs describe-file-systems --output table
  exit 0
fi

: "${CLUSTER_NAME:?}"

if [ "${LIST_EFS_SCOPE}" != "cluster" ]; then
  echo "ERROR: LIST_EFS_SCOPE must be 'cluster' or 'region' (got: ${LIST_EFS_SCOPE})"
  exit 1
fi

VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=tag:kubernetes.io/cluster/${CLUSTER_NAME},Values=owned" \
  --query 'Vpcs[0].VpcId' --output text)
if [ "${VPC_ID}" = "None" ] || [ -z "${VPC_ID}" ]; then
  echo "ERROR: No VPC found with tag kubernetes.io/cluster/${CLUSTER_NAME}=owned"
  exit 1
fi

echo "Cluster VPC: ${VPC_ID}"
echo ""
echo "FileSystemId	State	Name	Encrypted"
echo "------------	-----	----	---------"

found=0
for fs in $(aws efs describe-file-systems --query 'FileSystems[*].FileSystemId' --output text); do
  [ -z "${fs}" ] && continue
  subnet_ids=$(aws efs describe-mount-targets --file-system-id "${fs}" --query 'MountTargets[].SubnetId' --output text 2>/dev/null || true)
  matched=0
  for sub in ${subnet_ids}; do
    svpc=$(aws ec2 describe-subnets --subnet-ids "${sub}" --query 'Subnets[0].VpcId' --output text 2>/dev/null || true)
    if [ "${svpc}" = "${VPC_ID}" ]; then
      matched=1
      break
    fi
  done
  if [ "${matched}" -eq 1 ]; then
    found=1
    aws efs describe-file-systems --file-system-id "${fs}" \
      --query 'FileSystems[0].[FileSystemId,LifeCycleState,Name,Encrypted]' --output text
  fi
done

if [ "${found}" -eq 0 ]; then
  echo "(none matched this VPC)"
fi

echo ""
echo "Tip: EFS with no mount targets cannot be tied to a VPC here; from repo root run:"
echo "  LIST_EFS_SCOPE=region ./infra/efs/aws/list-efs.sh"
EOSCRIPT
)"

oc create configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --from-literal=list.sh="${LIST_SCRIPT}" >/dev/null

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
            - name: LIST_EFS_SCOPE
              value: "${LIST_EFS_SCOPE}"
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
          command: ["/bin/bash", "/scripts/list.sh"]
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

if ! oc wait --for=condition=complete "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" --timeout=120s; then
  echo "Job failed or timed out. Logs:"
  oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" || true
  oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
  oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
  exit 1
fi

oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}"
oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
