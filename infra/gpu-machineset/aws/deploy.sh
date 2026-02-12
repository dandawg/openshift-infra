#!/bin/bash
set -e

# GPU MachineSet Deployment Script for AWS
# This script deploys a GPU machineset via ArgoCD with proper Helm parameter configuration

# Default values
INSTANCE_TYPE=${INSTANCE_TYPE:-"g6.2xlarge"}
ROOT_VOLUME_SIZE=${ROOT_VOLUME_SIZE:-120}
ROOT_VOLUME_TYPE=${ROOT_VOLUME_TYPE:-"gp3"}
ROOT_VOLUME_IOPS=${ROOT_VOLUME_IOPS:-3000}
REPLICA_COUNT=${REPLICA_COUNT:-1}

# Determine GitOps file and app name based on instance type
case "$INSTANCE_TYPE" in
  "g4dn.xlarge")
    GITOPS_FILE="gitops/infra/gpu-machineset-aws-g4dn-xlarge.yaml"
    APP_NAME="gpu-machineset-aws-g4dn-xlarge"
    MACHINE_NAME_SUFFIX="g4dn"
    ;;
  "g6.2xlarge")
    GITOPS_FILE="gitops/infra/gpu-machineset-aws-g6.yaml"
    APP_NAME="gpu-machineset-aws-g6"
    MACHINE_NAME_SUFFIX="g6"
    ;;
  "g6.4xlarge")
    GITOPS_FILE="gitops/infra/gpu-machineset-aws-g6-4xlarge.yaml"
    APP_NAME="gpu-machineset-aws-g6-4xlarge"
    MACHINE_NAME_SUFFIX="g6-4x"
    ;;
  "g6e.2xlarge")
    GITOPS_FILE="gitops/infra/gpu-machineset-aws-g6e.yaml"
    APP_NAME="gpu-machineset-aws-g6e"
    MACHINE_NAME_SUFFIX="g6e"
    ;;
  *)
    echo "Error: Unsupported INSTANCE_TYPE '$INSTANCE_TYPE'"
    echo "Supported types: g4dn.xlarge, g6.2xlarge, g6.4xlarge, g6e.2xlarge"
    exit 1
    ;;
esac

echo "==================================="
echo "GPU MachineSet Deployment"
echo "==================================="
echo "Instance Type: $INSTANCE_TYPE"
echo "Root Volume: ${ROOT_VOLUME_SIZE}GB $ROOT_VOLUME_TYPE (${ROOT_VOLUME_IOPS} IOPS)"
echo "Replicas: $REPLICA_COUNT"
echo ""

# Step 1: Get cluster information
echo "Step 1: Gathering cluster information..."
CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
REGION=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')

# Try to get availability zone from worker machines first, fallback to master machines
AVAILABILITY_ZONE=$(oc get machines -n openshift-machine-api -l machine.openshift.io/cluster-api-machine-role=worker -o jsonpath='{.items[0].spec.providerSpec.value.placement.availabilityZone}' 2>/dev/null || true)
if [ -z "$AVAILABILITY_ZONE" ]; then
  echo "  No worker machines found, using master node configuration..."
  AVAILABILITY_ZONE=$(oc get machines -n openshift-machine-api -l machine.openshift.io/cluster-api-machine-role=master -o jsonpath='{.items[0].spec.providerSpec.value.placement.availabilityZone}')
fi

INFRA_ID=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')

# Try to get AMI from existing machinesets, fallback to machines
AMI_ID=$(oc get machineset -n openshift-machine-api -o json 2>/dev/null | jq -r '.items[] | select(.metadata.name | contains("gpu") | not) | .spec.template.spec.providerSpec.value.ami.id' | head -1 || true)
if [ -z "$AMI_ID" ]; then
  echo "  No machinesets found, using machine AMI configuration..."
  AMI_ID=$(oc get machines -n openshift-machine-api -o jsonpath='{.items[0].spec.providerSpec.value.ami.id}')
fi

echo "  Cluster Name: $CLUSTER_NAME"
echo "  Region: $REGION"
echo "  Availability Zone: $AVAILABILITY_ZONE"
echo "  Infrastructure ID: $INFRA_ID"
echo "  AMI ID: $AMI_ID"
echo ""

# Validate cluster info
if [ -z "$CLUSTER_NAME" ] || [ -z "$REGION" ] || [ -z "$AVAILABILITY_ZONE" ] || [ -z "$AMI_ID" ]; then
  echo "Error: Failed to retrieve cluster information. Is this an OpenShift cluster on AWS?"
  exit 1
fi

# Step 2: Login to ArgoCD
echo "Step 2: Logging in to ArgoCD..."
ARGOCD_PASSWORD=$(oc get secret/openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' | base64 -d)
ARGOCD_SERVER=$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')

if [ -z "$ARGOCD_SERVER" ]; then
  echo "Error: OpenShift GitOps not found. Please install OpenShift GitOps first."
  exit 1
fi

argocd login $ARGOCD_SERVER --username admin --password $ARGOCD_PASSWORD --insecure > /dev/null 2>&1
echo "  Logged in to ArgoCD at $ARGOCD_SERVER"
echo ""

# Step 3: Create the ArgoCD Application
echo "Step 3: Creating ArgoCD Application..."
if oc get application $APP_NAME -n openshift-gitops > /dev/null 2>&1; then
  echo "  Application '$APP_NAME' already exists. Skipping creation."
else
  oc apply -f $GITOPS_FILE
  echo "  Created application '$APP_NAME'"
fi
echo ""

# Step 4: Set Helm parameters
echo "Step 4: Setting Helm parameters..."
argocd app set $APP_NAME \
  -p clusterName="$CLUSTER_NAME" \
  -p region="$REGION" \
  -p availabilityZone="$AVAILABILITY_ZONE" \
  -p infraID="$INFRA_ID" \
  -p amiId="$AMI_ID" \
  -p rootVolume.size="$ROOT_VOLUME_SIZE" \
  -p rootVolume.type="$ROOT_VOLUME_TYPE" \
  -p rootVolume.iops="$ROOT_VOLUME_IOPS" \
  -p instanceType="$INSTANCE_TYPE" \
  -p machineNameSuffix="$MACHINE_NAME_SUFFIX" \
  -p replicas="$REPLICA_COUNT" > /dev/null 2>&1
echo "  Parameters configured"
echo ""

# Step 5: Enable auto-sync and sync
echo "Step 5: Syncing application..."
argocd app set $APP_NAME --sync-policy automated --auto-prune --self-heal > /dev/null 2>&1
argocd app sync $APP_NAME > /dev/null 2>&1
echo "  Application synced"
echo ""

echo "==================================="
echo "Deployment initiated successfully!"
echo "==================================="
echo ""
echo "Monitor progress with:"
echo "  oc get machineset -n openshift-machine-api -w"
echo "  oc get machine -n openshift-machine-api"
echo ""
echo "Wait for GPU node (5-10 minutes):"
echo "  oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s"
echo ""
echo "Verify GPU node:"
echo "  oc get nodes -l nvidia.com/gpu.present=true"
echo ""
