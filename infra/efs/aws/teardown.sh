#!/bin/bash
set -euo pipefail

# Tears down EFS RWX storage:
#   AWS side  — deletes mount targets, EFS security group, EFS filesystem (via Job using aws-cloud-credentials)
#   OpenShift — deletes the ArgoCD Application efs-aws (which prunes StorageClass, ClusterCSIDriver, Subscription)
#
# Prerequisites: oc, cluster-admin, OpenShift on AWS.
#
# Required environment variable:
#   EFS_FILE_SYSTEM_ID   The fs-xxxxx id to delete (printed at the end of deploy.sh).
#
# Optional environment variables:
#   SKIP_AWS_TEARDOWN    Set to "true" to skip the AWS side (only remove the Argo CD app / OpenShift objects).

APP_NAME="efs-aws"
# kube-system/aws-creds (the sandbox "student" admin) has elasticfilesystem:* permissions.
# openshift-machine-api/aws-cloud-credentials only covers EC2/machine operations — not EFS.
CREDS_NAMESPACE="kube-system"
SECRET_AWS_CREDS="aws-creds"
JOB_NAMESPACE="kube-system"

FS_ID="${EFS_FILE_SYSTEM_ID:-}"
SKIP_AWS_TEARDOWN="${SKIP_AWS_TEARDOWN:-false}"

if [ -z "${FS_ID}" ] && [ "${SKIP_AWS_TEARDOWN}" != "true" ]; then
  echo "Error: EFS_FILE_SYSTEM_ID is required."
  echo "  Usage: EFS_FILE_SYSTEM_ID=fs-xxxxxxxx $0"
  echo "  To only remove the Argo CD app and leave AWS intact: SKIP_AWS_TEARDOWN=true $0"
  exit 1
fi

echo "================================="
echo "AWS EFS RWX Teardown"
echo "================================="
if [ -n "${FS_ID}" ]; then
  echo "EFS FileSystemId: ${FS_ID}"
fi
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

if [ "${SKIP_AWS_TEARDOWN}" = "true" ]; then
  echo "Step 2: Skipping AWS teardown (SKIP_AWS_TEARDOWN=true)."
  echo ""
else
  echo "Step 2: Removing AWS EFS resources via Job in ${JOB_NAMESPACE}..."
  JOB_NAME="efs-teardown-$(date +%s)-${RANDOM}"
  CM_NAME="${JOB_NAME}-script"

  oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
  oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true

  TEARDOWN_SCRIPT="$(cat <<'EOSCRIPT'
set -euo pipefail
: "${REGION:?}"
: "${CLUSTER_NAME:?}"
: "${FS_ID:?}"
: "${AWS_ACCESS_KEY_ID:?}"
: "${AWS_SECRET_ACCESS_KEY:?}"
export AWS_DEFAULT_REGION="${REGION}"

echo "Deleting mount targets for ${FS_ID}..."
MT_IDS=$(aws efs describe-mount-targets \
  --file-system-id "${FS_ID}" \
  --query 'MountTargets[*].MountTargetId' --output text)

if [ -n "${MT_IDS}" ]; then
  for MT_ID in ${MT_IDS}; do
    echo "  Deleting mount target ${MT_ID}..."
    aws efs delete-mount-target --mount-target-id "${MT_ID}"
  done

  echo "  Waiting for mount targets to be deleted (this takes ~1-2 min)..."
  while true; do
    REMAINING=$(aws efs describe-mount-targets \
      --file-system-id "${FS_ID}" \
      --query 'MountTargets[*].MountTargetId' --output text || true)
    if [ -z "${REMAINING}" ]; then
      break
    fi
    echo "  Still deleting: ${REMAINING}"
    sleep 10
  done
  echo "  Mount targets deleted."
else
  echo "  No mount targets found."
fi

echo "Finding EFS security group (tag Name=${CLUSTER_NAME}-efs-nfs)..."
EFS_SG=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Name,Values=${CLUSTER_NAME}-efs-nfs" \
  --query 'SecurityGroups[0].GroupId' --output text)

echo "Deleting EFS filesystem ${FS_ID}..."
aws efs delete-file-system --file-system-id "${FS_ID}"
echo "  Filesystem deleted."

if [ -n "${EFS_SG}" ] && [ "${EFS_SG}" != "None" ]; then
  echo "Deleting EFS security group ${EFS_SG}..."
  aws ec2 delete-security-group --group-id "${EFS_SG}" || \
    echo "  Warning: Could not delete SG ${EFS_SG} (may still be referenced). Delete manually."
else
  echo "  EFS security group not found (may have been removed already)."
fi

echo "AWS EFS teardown complete."
EOSCRIPT
)"

  oc create configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --from-literal=teardown.sh="${TEARDOWN_SCRIPT}" >/dev/null

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
            - name: FS_ID
              value: "${FS_ID}"
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
          command: ["/bin/bash", "/scripts/teardown.sh"]
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

  if ! oc wait --for=condition=complete "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" --timeout=300s; then
    echo "Job failed or timed out. Logs:"
    oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}" || true
    oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
    oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
    exit 1
  fi

  oc logs "job/${JOB_NAME}" -n "${JOB_NAMESPACE}"
  oc delete job "${JOB_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
  oc delete configmap "${CM_NAME}" -n "${JOB_NAMESPACE}" --ignore-not-found >/dev/null
  echo ""
fi

echo "Step 3: Removing Argo CD Application '${APP_NAME}'..."
if oc get application "${APP_NAME}" -n openshift-gitops >/dev/null 2>&1; then
  # Disable auto-sync first so deletion of the app also prunes its child resources
  argocd_available=false
  ARGOCD_SERVER="$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}' 2>/dev/null || true)"
  if [ -n "${ARGOCD_SERVER}" ]; then
    ARGOCD_PASSWORD="$(oc get secret/openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' | base64 -d)"
    if argocd login "${ARGOCD_SERVER}" --username admin --password "${ARGOCD_PASSWORD}" --insecure >/dev/null 2>&1; then
      argocd_available=true
    fi
  fi

  if [ "${argocd_available}" = "true" ]; then
    # Cascade delete through Argo CD (prunes StorageClass, ClusterCSIDriver, Subscription)
    argocd app delete "${APP_NAME}" --cascade --yes >/dev/null 2>&1 || true
    echo "  Argo CD Application '${APP_NAME}' deleted (resources pruned)."
  else
    echo "  Warning: Could not reach Argo CD; deleting Application object directly (child resources may remain)."
    oc delete application "${APP_NAME}" -n openshift-gitops --ignore-not-found
    echo "  If StorageClass / ClusterCSIDriver remain, remove them manually:"
    echo "    oc delete storageclass efs-csi"
    echo "    oc delete clusterscsidriver efs.csi.aws.com"
    echo "    oc delete subscription aws-efs-csi-driver-operator -n openshift-cluster-csi-drivers"
  fi
else
  echo "  Application '${APP_NAME}' not found (already removed)."
fi
echo ""

echo "================================="
echo "Teardown complete."
echo "================================="
echo ""
echo "Verify:"
echo "  oc get storageclass efs-csi 2>/dev/null && echo 'StorageClass still present' || echo 'StorageClass removed'"
echo "  oc get application ${APP_NAME} -n openshift-gitops 2>/dev/null && echo 'App still present' || echo 'App removed'"
echo ""
