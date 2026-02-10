#!/bin/bash
set -e

# CPU Worker MachineSet Deployment Script for AWS
# This script deploys a CPU worker machineset

# Default values
INSTANCE_TYPE=${INSTANCE_TYPE:-"m6a.4xlarge"}
ROOT_VOLUME_SIZE=${ROOT_VOLUME_SIZE:-120}
ROOT_VOLUME_TYPE=${ROOT_VOLUME_TYPE:-"gp3"}
ROOT_VOLUME_IOPS=${ROOT_VOLUME_IOPS:-3000}
REPLICA_COUNT=${REPLICA_COUNT:-1}

echo "==================================="
echo "CPU Worker MachineSet Deployment"
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

# Try to get AMI from existing machinesets, fallback to machines
AMI_ID=$(oc get machineset -n openshift-machine-api -o json 2>/dev/null | jq -r '.items[] | select(.metadata.name | contains("gpu") | not) | .spec.template.spec.providerSpec.value.ami.id' | head -1 || true)
if [ -z "$AMI_ID" ] || [ "$AMI_ID" == "null" ]; then
  echo "  No machinesets found, using machine AMI configuration..."
  AMI_ID=$(oc get machines -n openshift-machine-api -o jsonpath='{.items[0].spec.providerSpec.value.ami.id}')
fi

# Get subnet configuration from existing machines (could be ID or filters)
SUBNET_CONFIG=$(oc get machines -n openshift-machine-api -o json | jq -r '.items[0].spec.providerSpec.value.subnet')

# Get security groups from existing machines
SECURITY_GROUPS=$(oc get machines -n openshift-machine-api -o json | jq -r '.items[0].spec.providerSpec.value.securityGroups')

echo "  Cluster Name: $CLUSTER_NAME"
echo "  Region: $REGION"
echo "  Availability Zone: $AVAILABILITY_ZONE"
echo "  AMI ID: $AMI_ID"
echo "  Subnet Config: $SUBNET_CONFIG"
echo "  Security Groups: $SECURITY_GROUPS"
echo ""

# Validate cluster info
if [ -z "$CLUSTER_NAME" ] || [ -z "$REGION" ] || [ -z "$AVAILABILITY_ZONE" ] || [ -z "$AMI_ID" ]; then
  echo "Error: Failed to retrieve cluster information. Is this an OpenShift cluster on AWS?"
  exit 1
fi

# Step 2: Create MachineSet
echo "Step 2: Creating CPU Worker MachineSet..."

# Create a unique machineset name that includes instance type to avoid conflicts
INSTANCE_TYPE_SUFFIX=$(echo "$INSTANCE_TYPE" | sed 's/\./-/g')
MACHINESET_NAME="${CLUSTER_NAME}-worker-${INSTANCE_TYPE_SUFFIX}-${AVAILABILITY_ZONE}"

# Convert security groups JSON to YAML format for the MachineSet
SECURITY_GROUPS_YAML=$(echo "$SECURITY_GROUPS" | jq -r 'map("            - " + (if .filters then ("filters:\n                - name: " + .filters[0].name + "\n                  values:\n                    - " + .filters[0].values[0]) else ("id: " + .id) end)) | join("\n")')

# Convert subnet JSON to YAML format for the MachineSet
SUBNET_YAML=$(echo "$SUBNET_CONFIG" | jq -r 'if .filters then "            filters:\n              - name: " + .filters[0].name + "\n                values:\n                  - " + .filters[0].values[0] else "            id: " + .id end')

cat <<EOF | oc apply -f -
apiVersion: machine.openshift.io/v1beta1
kind: MachineSet
metadata:
  labels:
    machine.openshift.io/cluster-api-cluster: ${CLUSTER_NAME}
  name: ${MACHINESET_NAME}
  namespace: openshift-machine-api
spec:
  replicas: ${REPLICA_COUNT}
  selector:
    matchLabels:
      machine.openshift.io/cluster-api-cluster: ${CLUSTER_NAME}
      machine.openshift.io/cluster-api-machineset: ${MACHINESET_NAME}
  template:
    metadata:
      labels:
        machine.openshift.io/cluster-api-cluster: ${CLUSTER_NAME}
        machine.openshift.io/cluster-api-machine-role: worker
        machine.openshift.io/cluster-api-machine-type: worker
        machine.openshift.io/cluster-api-machineset: ${MACHINESET_NAME}
    spec:
      metadata:
        labels:
          node-role.kubernetes.io/worker: ""
      providerSpec:
        value:
          ami:
            id: ${AMI_ID}
          apiVersion: machine.openshift.io/v1beta1
          blockDevices:
            - ebs:
                iops: ${ROOT_VOLUME_IOPS}
                volumeSize: ${ROOT_VOLUME_SIZE}
                volumeType: ${ROOT_VOLUME_TYPE}
          credentialsSecret:
            name: aws-cloud-credentials
          deviceIndex: 0
          iamInstanceProfile:
            id: ${CLUSTER_NAME}-worker-profile
          instanceType: ${INSTANCE_TYPE}
          kind: AWSMachineProviderConfig
          metadata:
            creationTimestamp: null
          placement:
            availabilityZone: ${AVAILABILITY_ZONE}
            region: ${REGION}
          securityGroups:
${SECURITY_GROUPS_YAML}
          subnet:
${SUBNET_YAML}
          tags:
            - name: kubernetes.io/cluster/${CLUSTER_NAME}
              value: owned
          userDataSecret:
            name: worker-user-data
EOF

echo "  MachineSet '${MACHINESET_NAME}' created"
echo ""

echo "==================================="
echo "Deployment completed successfully!"
echo "==================================="
echo ""
echo "Monitor progress with:"
echo "  oc get machineset -n openshift-machine-api -w"
echo "  oc get machine -n openshift-machine-api"
echo ""
echo "Wait for worker node (5-10 minutes):"
echo "  oc wait --for=condition=Ready nodes -l node-role.kubernetes.io/worker --timeout=600s"
echo ""
echo "Verify worker node:"
echo "  oc get nodes -l node-role.kubernetes.io/worker"
echo ""
